import json
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.core.config import DEEPSEEK_MODEL
from app.schemas.action_schema import (
    AnnualLeaveActionProposal,
    AnnualLeaveClarification,
    ClarificationPlanningResult,
    InvalidPlanningResult,
    ProposalPlanningResult,
    ToolPlanningResult,
)
from app.services.annual_leave_input_service import (
    AnnualLeaveInputError,
    analyze_annual_leave_input,
    build_leave_continuation_state,
    clarification_question,
)
from app.services.llm_service import _get_controlled_tool_client

for logger_name in ("httpx", "httpcore", "openai", "openai._base_client"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)


TOOL_NAME = "plan_annual_leave_request"
FORCED_ANNUAL_LEAVE_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": TOOL_NAME},
}
ANNUAL_LEAVE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Enter the controlled annual leave planning flow after "
            "the application has deterministically validated the "
            "request fields. This tool never submits, approves, "
            "updates, cancels, or executes a leave request."
        ),
    },
}

SYSTEM_MESSAGE = (
    "A controlled annual leave request has passed "
    "deterministic input validation. Call only "
    "plan_annual_leave_request. The tool has no "
    "arguments and performs no write operation."
)
USER_MESSAGE = "Enter the controlled annual leave planning flow."


def _invalid(error_code: str) -> InvalidPlanningResult:
    return InvalidPlanningResult(error_code=error_code)


def _validate_response(response: Any) -> InvalidPlanningResult | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return _invalid("tool_call_missing")
    message = getattr(choices[0], "message", None)
    calls = getattr(message, "tool_calls", None) if message is not None else None
    if not calls:
        return _invalid("tool_call_missing")
    if len(calls) != 1:
        return _invalid("tool_call_count_invalid")
    call = calls[0]
    if getattr(call, "type", None) != "function":
        return _invalid("tool_call_invalid")
    function = getattr(call, "function", None)
    if function is None or getattr(function, "name", None) != TOOL_NAME:
        return _invalid("tool_name_not_allowed")
    arguments = getattr(function, "arguments", None)
    if not isinstance(arguments, str):
        return _invalid("tool_arguments_invalid")
    try:
        decoded = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return _invalid("tool_arguments_invalid")
    if not isinstance(decoded, dict) or decoded:
        return _invalid("tool_arguments_invalid")
    return None


def plan_annual_leave_action(
    question: str,
    *,
    business_date: date,
    policy_context: str = "",
    trace_id: str = "",
    continuation_state: dict | None = None,
    completion_create: Callable[..., Any] | None = None,
) -> ToolPlanningResult:
    del policy_context, trace_id  # Never send business or tracing data to the model.
    try:
        analysis = analyze_annual_leave_input(
            question,
            business_date=business_date,
            continuation_state=continuation_state,
        )
    except AnnualLeaveInputError:
        return _invalid("tool_arguments_invalid")
    if analysis.missing_fields:
        return ClarificationPlanningResult(
            clarification=AnnualLeaveClarification(
                missing_fields=analysis.missing_fields,
                question=clarification_question(analysis.missing_fields),
                continuation_state=build_leave_continuation_state(analysis),
            )
        )

    create = completion_create or _get_controlled_tool_client().chat.completions.create
    try:
        response = create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": USER_MESSAGE},
            ],
            tools=[deepcopy(ANNUAL_LEAVE_TOOL)],
            tool_choice=deepcopy(FORCED_ANNUAL_LEAVE_TOOL_CHOICE),
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=64,
        )
        invalid = _validate_response(response)
        if invalid is not None:
            return invalid
        return ProposalPlanningResult(
            proposal=AnnualLeaveActionProposal(
                action_type="ANNUAL_LEAVE_REQUEST",
                start_date=analysis.start_date,
                end_date=analysis.end_date,
                reason=analysis.reason_evidence,
                half_day=analysis.half_day,
            )
        )
    except APITimeoutError:
        return _invalid("provider_timeout")
    except APIConnectionError:
        return _invalid("provider_unavailable")
    except APIStatusError:
        return _invalid("provider_unavailable")
