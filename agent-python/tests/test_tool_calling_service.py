import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError

from app.services.tool_calling_service import TOOL_NAME, plan_annual_leave_action


def tool_call(arguments, *, name=TOOL_NAME, call_type="function"):
    return SimpleNamespace(
        type=call_type,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def completion(calls=None, *, content=None, choices=True):
    message = SimpleNamespace(tool_calls=calls, content=content)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)] if choices else [])
    return lambda **_: response


def proposal_arguments(**changes):
    values = {
        "result_type": "PROPOSAL",
        "start_date": "2026-07-20",
        "end_date": "2026-07-20",
        "reason": "私事",
        "half_day": "NONE",
        "missing_fields": [],
        "clarification_question": "",
    }
    values.update(changes)
    return json.dumps(values, ensure_ascii=False)


def clarification_arguments(**changes):
    values = {
        "result_type": "CLARIFICATION",
        "start_date": "",
        "end_date": "",
        "reason": "私事",
        "half_day": "NONE",
        "missing_fields": ["start_date"],
        "clarification_question": "请补充申请日期。",
    }
    values.update(changes)
    return json.dumps(values, ensure_ascii=False)


def plan(create):
    return plan_annual_leave_action(
        "request", business_date=date(2026, 7, 16), completion_create=create
    )


def test_provider_request_profile_is_pinned():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return completion([tool_call(proposal_arguments())])()

    assert plan(create).kind == "proposal"
    assert len(captured["tools"]) == 1
    assert captured["tool_choice"] == "required"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    for forbidden in (
        "temperature", "response_format", "reasoning_effort", "strict", "stream",
        "frequency_penalty", "presence_penalty",
    ):
        assert forbidden not in captured
    assert "strict" not in captured["tools"][0]["function"]


def test_valid_proposal():
    result = plan(completion([tool_call(proposal_arguments(reason="  私事  "))]))
    assert result.kind == "proposal"
    assert result.proposal.start_date == date(2026, 7, 20)
    assert result.proposal.reason == "私事"


def test_valid_clarification_deduplicates_stably():
    result = plan(completion([tool_call(clarification_arguments(
        missing_fields=["start_date", "start_date", "reason"]
    ))]))
    assert result.kind == "clarification"
    assert result.clarification.missing_fields == ["start_date", "reason"]


@pytest.mark.parametrize("calls", [None, []])
def test_missing_tool_calls(calls):
    assert plan(completion(calls)).error_code == "tool_call_missing"


def test_missing_choices():
    assert plan(completion(choices=False)).error_code == "tool_call_missing"


def test_multiple_tool_calls():
    result = plan(completion([tool_call(proposal_arguments()), tool_call(proposal_arguments())]))
    assert result.error_code == "tool_call_count_invalid"


def test_non_function_tool_call():
    assert plan(completion([tool_call(proposal_arguments(), call_type="custom")])).error_code == "tool_call_invalid"


def test_unknown_tool_name():
    assert plan(completion([tool_call(proposal_arguments(), name="submit_leave_request")])).error_code == "tool_name_not_allowed"


@pytest.mark.parametrize("arguments", ["{", "[]", '"text"', "null"])
def test_arguments_must_be_json_object(arguments):
    assert plan(completion([tool_call(arguments)])).error_code == "tool_arguments_invalid"


@pytest.mark.parametrize("changes", [
    {"unexpected": "value"},
    {"start_date": "invalid"},
    {"end_date": "2026-99-99"},
    {"reason": ""},
    {"reason": "x" * 201},
    {"missing_fields": ["reason"]},
    {"clarification_question": "should be empty"},
])
def test_invalid_proposal_arguments(changes):
    assert plan(completion([tool_call(proposal_arguments(**changes))])).kind == "invalid"


@pytest.mark.parametrize("changes", [
    {"missing_fields": []},
    {"clarification_question": ""},
    {"clarification_question": "x" * 201},
    {"missing_fields": ["employeeId"]},
])
def test_invalid_clarification_arguments(changes):
    assert plan(completion([tool_call(clarification_arguments(**changes))])).kind == "invalid"


def test_free_text_success_claim_is_not_parsed():
    result = plan(completion([], content="申请已提交"))
    assert result.kind == "invalid"


def test_provider_timeout_is_stable():
    request = httpx.Request("POST", "https://example.invalid")

    def create(**_):
        raise APITimeoutError(request=request)

    assert plan(create).error_code == "provider_timeout"


def test_provider_connection_error_is_stable():
    request = httpx.Request("POST", "https://example.invalid")

    def create(**_):
        raise APIConnectionError(request=request)

    assert plan(create).error_code == "provider_unavailable"


def test_results_never_contain_java_control_fields():
    serialized = plan(completion([tool_call(proposal_arguments())])).model_dump_json()
    assert "actionId" not in serialized
    assert "confirmationNonce" not in serialized
    assert "requestId" not in serialized
