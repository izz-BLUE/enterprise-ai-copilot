import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.services.tool_calling_service import (
    SYSTEM_MESSAGE,
    TOOL_NAME,
    USER_MESSAGE,
    plan_annual_leave_action,
)


BUSINESS_DATE = date(2026, 7, 16)
COMPLETE = "申请2026-07-20一天年假，原因为私事"


def tool_call(arguments="{}", *, name=TOOL_NAME, call_type="function"):
    return SimpleNamespace(
        type=call_type,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def completion(calls=None, *, content=None, choices=True):
    message = SimpleNamespace(tool_calls=calls, content=content)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)] if choices else [])
    return lambda **_: response


def plan(question, create):
    return plan_annual_leave_action(
        question,
        business_date=BUSINESS_DATE,
        policy_context="do-not-send-policy",
        trace_id="do-not-send-trace",
        completion_create=create,
    )


@pytest.mark.parametrize(
    ("question", "expected_fields", "expected_question"),
    [
        ("申请一天年假，原因为私事", ["start_date", "end_date"], "请提供明确的年假日期。"),
        ("申请2026-07-20一天年假", ["reason"], "请补充年假申请原因。"),
        (
            "申请一天年假",
            ["start_date", "end_date", "reason"],
            "请补充明确的年假日期和申请原因。",
        ),
    ],
)
def test_missing_fields_do_not_call_provider(question, expected_fields, expected_question):
    def forbidden(**_):
        raise AssertionError("provider must not be called")

    result = plan(question, forbidden)
    assert result.kind == "clarification"
    assert result.clarification.missing_fields == expected_fields
    assert result.clarification.question == expected_question


def test_complete_request_calls_provider_once_with_fixed_zero_argument_contract():
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return completion([tool_call()])()

    assert plan(COMPLETE, create).kind == "proposal"
    assert len(captured) == 1
    request = captured[0]
    assert request["messages"] == [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": USER_MESSAGE},
    ]
    assert request["tool_choice"] == {
        "type": "function",
        "function": {"name": TOOL_NAME},
    }
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["max_tokens"] == 64
    assert len(request["tools"]) == 1
    function = request["tools"][0]["function"]
    assert set(function) == {"name", "description"}
    assert function["name"] == TOOL_NAME
    serialized = json.dumps(request, ensure_ascii=False)
    for forbidden in (
        COMPLETE,
        "2026-07-20",
        "私事",
        "half_day",
        "date_evidence",
        "reason_evidence",
        "do-not-send-policy",
        "do-not-send-trace",
    ):
        assert forbidden not in serialized
    for omitted in (
        "temperature",
        "response_format",
        "reasoning_effort",
        "stream",
        "strict",
    ):
        assert omitted not in request


@pytest.mark.parametrize(
    ("question", "start_date", "end_date", "reason", "half_day"),
    [
        (COMPLETE, date(2026, 7, 20), date(2026, 7, 20), "私事", "NONE"),
        ("申请2026年7月20日一天年假，原因为私事", date(2026, 7, 20), date(2026, 7, 20), "私事", "NONE"),
        ("明天申请一天年假，因为需要就医", date(2026, 7, 17), date(2026, 7, 17), "需要就医", "NONE"),
        ("申请2026-07-20至2026-07-22年假，原因为家庭事务", date(2026, 7, 20), date(2026, 7, 22), "家庭事务", "NONE"),
        ("申请2026-07-20上午半天年假，原因为就医", date(2026, 7, 20), date(2026, 7, 20), "就医", "AM"),
        ("申请2026-07-20下午半天年假，原因为就医", date(2026, 7, 20), date(2026, 7, 20), "就医", "PM"),
    ],
)
def test_proposal_is_built_only_from_deterministic_analysis(
    question, start_date, end_date, reason, half_day
):
    result = plan(question, completion([tool_call()]))
    assert result.kind == "proposal"
    assert result.proposal.start_date == start_date
    assert result.proposal.end_date == end_date
    assert result.proposal.reason == reason
    assert result.proposal.half_day == half_day


def test_provider_cannot_override_business_fields():
    result = plan(COMPLETE, completion([tool_call('{"reason":"provider-value"}')]))
    assert result.error_code == "tool_arguments_invalid"


@pytest.mark.parametrize("calls", [None, []])
def test_missing_tool_call_is_rejected_without_content_fallback(calls):
    result = plan(COMPLETE, completion(calls, content='{"approved":true}'))
    assert result.error_code == "tool_call_missing"


def test_missing_choices_and_multiple_calls_are_rejected():
    assert plan(COMPLETE, completion(choices=False)).error_code == "tool_call_missing"
    result = plan(COMPLETE, completion([tool_call(), tool_call()]))
    assert result.error_code == "tool_call_count_invalid"


def test_wrong_type_and_name_are_rejected():
    wrong_type = plan(COMPLETE, completion([tool_call(call_type="custom")]))
    wrong_name = plan(COMPLETE, completion([tool_call(name="submit_leave_request")]))
    assert wrong_type.error_code == "tool_call_invalid"
    assert wrong_name.error_code == "tool_name_not_allowed"


@pytest.mark.parametrize(
    "arguments",
    ["", "{", "[]", '"text"', "null", None, '{"approved":true}'],
)
def test_arguments_must_be_valid_empty_json_object(arguments):
    result = plan(COMPLETE, completion([tool_call(arguments)]))
    assert result.error_code == "tool_arguments_invalid"


def test_provider_timeout_connection_and_status_are_stable():
    request = httpx.Request("POST", "https://example.invalid")

    def timeout(**_):
        raise APITimeoutError(request=request)

    def connection(**_):
        raise APIConnectionError(request=request)

    response = httpx.Response(503, request=request)

    def status(**_):
        raise APIStatusError("provider status error", response=response, body=None)

    assert plan(COMPLETE, timeout).error_code == "provider_timeout"
    assert plan(COMPLETE, connection).error_code == "provider_unavailable"
    assert plan(COMPLETE, status).error_code == "provider_unavailable"


@pytest.mark.parametrize("error", [RuntimeError("bug"), AttributeError("bug")])
def test_programming_errors_are_not_swallowed_or_retried(error):
    calls = 0

    def create(**_):
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(type(error), match="bug"):
        plan(COMPLETE, create)
    assert calls == 1


def test_proposal_never_contains_java_control_fields():
    serialized = plan(COMPLETE, completion([tool_call()])).model_dump_json()
    for forbidden in ("actionId", "confirmationNonce", "employeeId", "requestId"):
        assert forbidden not in serialized
