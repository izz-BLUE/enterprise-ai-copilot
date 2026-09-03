import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.schemas.action_schema import ProposalPlanningResult
from app.services import llm_service, tool_calling_service
from app.services.annual_leave_input_service import AnnualLeaveInputAnalysis
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


def test_controlled_client_disables_sdk_retries(monkeypatch):
    monkeypatch.setattr(llm_service, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_service, "DEEPSEEK_BASE_URL", "https://provider.test/v1")
    monkeypatch.setattr(llm_service, "DEEPSEEK_MODEL", "test-model")
    monkeypatch.setattr(llm_service, "_controlled_tool_client", None)

    client = llm_service._get_controlled_tool_client()

    assert client.max_retries == 0


@pytest.mark.parametrize("failure", ["status", "connection"])
def test_real_sdk_transport_makes_one_http_attempt(monkeypatch, failure):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if failure == "connection":
            raise httpx.ConnectError("test connection failure", request=request)
        return httpx.Response(500, json={"error": {"message": "test failure"}})

    transport = httpx.MockTransport(handler)
    real_openai = OpenAI

    def openai_with_mock_transport(**kwargs):
        return real_openai(
            **kwargs,
            http_client=httpx.Client(transport=transport),
        )

    monkeypatch.setattr(llm_service, "OpenAI", openai_with_mock_transport)
    monkeypatch.setattr(llm_service, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_service, "DEEPSEEK_BASE_URL", "https://provider.test/v1")
    monkeypatch.setattr(llm_service, "DEEPSEEK_MODEL", "test-model")
    monkeypatch.setattr(llm_service, "_controlled_tool_client", None)
    monkeypatch.setattr(tool_calling_service, "DEEPSEEK_MODEL", "test-model")

    result = plan_annual_leave_action(
        COMPLETE,
        business_date=BUSINESS_DATE,
    )

    assert result.error_code == "provider_unavailable"
    assert attempts == 1


def test_real_sdk_serialized_request_contains_only_protocol_data(monkeypatch):
    request_count = 0
    tool_arguments = "{}"
    question = (
        "question-canary-annual-leave "
        "2026-11-17 2026-11-18 "
        "reason-canary-private-family-event "
        "duration-canary-2-days "
        "employee-canary-DEMO-002 "
        "display-name-canary-user-b "
        "demo-user-canary-DEMO-002 "
        "balance-canary-7.5 "
        "admin-token-canary-private "
        "nonce-canary-private "
        "idempotency-canary-private"
    )
    forbidden_keys = {
        "startDate", "start_date", "endDate", "end_date", "reason",
        "halfDay", "half_day", "days", "employeeId", "employee_id",
        "displayName", "display_name", "demoUserId", "demo_user_id",
        "balance", "leaveBalance", "leave_balance", "traceId", "trace_id",
        "adminToken", "admin_token", "confirmationNonce", "confirmation_nonce",
        "idempotencyKey", "idempotency_key", "actionId", "action_id",
        "requestId", "request_id",
    }
    forbidden_values = [
        "question-canary-annual-leave",
        "2026-11-17",
        "2026-11-18",
        "reason-canary-private-family-event",
        "duration-canary-2-days",
        "employee-canary-DEMO-002",
        "display-name-canary-user-b",
        "demo-user-canary-DEMO-002",
        "balance-canary-7.5",
        "policy-context-business-canary",
        "trace-canary-private",
        "admin-token-canary-private",
        "nonce-canary-private",
        "idempotency-canary-private",
    ]

    def collect_keys(value):
        keys = set()
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(key)
                keys.update(collect_keys(child))
        elif isinstance(value, list):
            for child in value:
                keys.update(collect_keys(child))
        return keys

    def handler(request):
        nonlocal request_count
        request_count += 1
        raw_body = request.content.decode("utf-8")
        payload = json.loads(raw_body)

        assert request.method == "POST"
        assert request.url.path.endswith("/chat/completions")
        assert payload["model"] == "test-model"
        assert payload["messages"] == [
            {"role": "system", "content": tool_calling_service.SYSTEM_MESSAGE},
            {"role": "user", "content": tool_calling_service.USER_MESSAGE},
        ]
        assert payload["tools"] == [tool_calling_service.ANNUAL_LEAVE_TOOL]
        function = payload["tools"][0]["function"]
        assert function["name"] == tool_calling_service.TOOL_NAME
        assert "description" in function
        assert "parameters" not in function
        assert "strict" not in function
        assert payload["tool_choice"] == (
            tool_calling_service.FORCED_ANNUAL_LEAVE_TOOL_CHOICE
        )
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 64
        assert collect_keys(payload).isdisjoint(forbidden_keys)
        for value in forbidden_values:
            assert value not in raw_body
        assert tool_calling_service.SYSTEM_MESSAGE in raw_body
        assert tool_calling_service.USER_MESSAGE in raw_body
        assert tool_calling_service.TOOL_NAME in raw_body

        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl-controlled-tool-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call-controlled-tool-test",
                            "type": "function",
                            "function": {
                                "name": tool_calling_service.TOOL_NAME,
                                "arguments": tool_arguments,
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    analysis = AnnualLeaveInputAnalysis(
        normalized_question=question,
        date_evidence=["2026-11-17", "2026-11-18"],
        start_date=date(2026, 11, 17),
        end_date=date(2026, 11, 18),
        reason_evidence="reason-canary-private-family-event",
        half_day="NONE",
        missing_fields=[],
    )
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAI(
            api_key="test-controlled-tool-key",
            base_url="https://controlled-tool.test/v1",
            max_retries=0,
            timeout=1.0,
            http_client=http_client,
        )
        monkeypatch.setattr(
            tool_calling_service,
            "_get_controlled_tool_client",
            lambda: client,
        )
        monkeypatch.setattr(tool_calling_service, "DEEPSEEK_MODEL", "test-model")
        monkeypatch.setattr(
            tool_calling_service,
            "analyze_annual_leave_input",
            lambda *_args, **_kwargs: analysis,
        )

        result = plan_annual_leave_action(
            question,
            business_date=date(2026, 11, 16),
            policy_context="policy-context-business-canary",
            trace_id="trace-canary-private",
        )

    assert isinstance(result, ProposalPlanningResult)
    assert request_count == 1
    assert json.loads(tool_arguments) == {}
    assert result.proposal.start_date == date(2026, 11, 17)
    assert result.proposal.end_date == date(2026, 11, 18)
    assert result.proposal.reason == "reason-canary-private-family-event"


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


@pytest.mark.parametrize(
    "question",
    [
        "申请2026-07-20一天年假，原因家里有事",
        "申请2026-07-20一天年假，原因：家里有事",
        "申请2026-07-20一天年假，原因是家里有事",
        "申请2026-07-20一天年假，因为家里有事",
    ],
)
def test_common_reason_prefixes_are_supported(question):
    result = plan(question, completion([tool_call()]))

    assert result.kind == "proposal"
    assert result.proposal.reason == "家里有事"


def test_leave_continuation_merges_current_reason_with_absolute_date_slots():
    result = plan_annual_leave_action(
        "家里有事",
        business_date=BUSINESS_DATE,
        continuation_state={
            "continuation_type": "leave_clarification",
            "start_date": "2026-07-17",
            "end_date": "2026-07-17",
            "half_day": "NONE",
            "waiting_for": "reason",
            "missing_fields": ["reason"],
        },
        completion_create=completion([tool_call()]),
    )

    assert result.kind == "proposal"
    assert result.proposal.start_date == date(2026, 7, 17)
    assert result.proposal.end_date == date(2026, 7, 17)
    assert result.proposal.half_day == "NONE"
    assert result.proposal.reason == "家里有事"


def _continuation(result):
    assert result.kind == "clarification"
    return result.clarification.continuation_state


def test_leave_case_2_reason_continuation_preserves_date_across_business_date_change():
    first = plan_annual_leave_action(
        "帮我申请明天一天年假",
        business_date=date(2026, 7, 16),
        completion_create=completion([tool_call()]),
    )
    second = plan_annual_leave_action(
        "家里有事",
        business_date=date(2026, 7, 17),
        continuation_state=_continuation(first),
        completion_create=completion([tool_call()]),
    )

    assert second.kind == "proposal"
    assert second.proposal.start_date == date(2026, 7, 17)
    assert second.proposal.end_date == date(2026, 7, 17)
    assert second.proposal.reason == "家里有事"


def test_leave_case_3_date_continuation_preserves_reason():
    first = plan_annual_leave_action(
        "帮我申请年假，原因家里有事",
        business_date=BUSINESS_DATE,
        completion_create=completion([tool_call()]),
    )
    second = plan_annual_leave_action(
        "明天一天",
        business_date=BUSINESS_DATE,
        continuation_state=_continuation(first),
        completion_create=completion([tool_call()]),
    )

    assert second.kind == "proposal"
    assert second.proposal.start_date == date(2026, 7, 17)
    assert second.proposal.end_date == date(2026, 7, 17)
    assert second.proposal.reason == "家里有事"


def test_leave_case_4_reason_continuation_preserves_pm():
    first = plan_annual_leave_action(
        "帮我申请明天下午半天年假",
        business_date=BUSINESS_DATE,
        completion_create=completion([tool_call()]),
    )
    second = plan_annual_leave_action(
        "家里有事",
        business_date=BUSINESS_DATE,
        continuation_state=_continuation(first),
        completion_create=completion([tool_call()]),
    )

    assert second.kind == "proposal"
    assert second.proposal.half_day == "PM"


def test_leave_case_5_ambiguous_half_day_remains_clarification_until_am_or_pm():
    first = plan_annual_leave_action(
        "帮我申请明天半天年假",
        business_date=BUSINESS_DATE,
        completion_create=completion([tool_call()]),
    )
    assert first.kind == "clarification"
    assert first.clarification.missing_fields == ["reason", "half_day"]
    assert first.clarification.continuation_state["half_day"] is None

    with_reason = plan_annual_leave_action(
        "家里有事",
        business_date=BUSINESS_DATE,
        continuation_state=_continuation(first),
        completion_create=completion([tool_call()]),
    )
    assert with_reason.kind == "clarification"
    assert with_reason.clarification.missing_fields == ["half_day"]

    complete = plan_annual_leave_action(
        "下午",
        business_date=BUSINESS_DATE,
        continuation_state=_continuation(with_reason),
        completion_create=completion([tool_call()]),
    )
    assert complete.kind == "proposal"
    assert complete.proposal.start_date == date(2026, 7, 17)
    assert complete.proposal.half_day == "PM"
    assert complete.proposal.reason == "家里有事"


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
