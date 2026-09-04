"""Planner Shadow Routing side path.

The shadow call observes a possible route for an eligible first request.  It
never executes a Tool and never contributes fields to AgentState or the
formal Planner result.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import get_args

from pydantic import ValidationError

from app.agents.workflow_guard.contracts import (
    DomainContext,
    DomainToolCallRejected,
    WorkflowGuard,
)
from app.core.observability import record_routing_shadow
from app.schemas.planner_schema import (
    Action,
    PlannerDecision,
    PlannerDecisionError,
    ReasonCode,
    ToolName,
)
from app.services.llm_service import LLMProviderError, call_llm

SHADOW_ROUTING_TIMEOUT_SECONDS = 3.0
SHADOW_ROUTING_DEADLINE_MARGIN_SECONDS = 0.1

_ACTION_VALUES = frozenset(get_args(Action))
_TOOL_VALUES = frozenset(get_args(ToolName))
_REASON_VALUES = frozenset(get_args(ReasonCode))


@dataclass(frozen=True)
class ShadowRoutingResult:
    """Internal, non-persistent result of one shadow observation."""

    action: str | None
    tool_name: str | None
    reason_code: str | None
    valid: bool
    guard_allowed: bool | None
    error_code: str | None
    disagreement: bool


def _safe_value(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _disagreement(
    legacy_action: str | None,
    legacy_tool: str | None,
    shadow_action: str | None,
    shadow_tool: str | None,
) -> bool:
    return legacy_action != shadow_action or legacy_tool != shadow_tool


def _emit_telemetry(
    result: ShadowRoutingResult,
    *,
    legacy_action: str | None,
    legacy_tool: str | None,
) -> None:
    attributes: dict[str, object] = {
        'routing.shadow.enabled': True,
        'routing.legacy_action': _safe_value(legacy_action, _ACTION_VALUES),
        'routing.legacy_tool': _safe_value(legacy_tool, _TOOL_VALUES),
        'routing.shadow_action': _safe_value(result.action, _ACTION_VALUES),
        'routing.shadow_tool': _safe_value(result.tool_name, _TOOL_VALUES),
        'routing.shadow_reason_code': _safe_value(result.reason_code, _REASON_VALUES),
        'routing.shadow_valid': result.valid,
        'routing.shadow_guard_allowed': result.guard_allowed,
        'routing.disagreement': result.disagreement,
        'routing.shadow_error_code': result.error_code,
    }
    try:
        record_routing_shadow(attributes)
    except Exception:
        # Observability is deliberately outside the formal business path.
        return


def _result(
    *,
    legacy_action: str | None,
    legacy_tool: str | None,
    action: str | None = None,
    tool_name: str | None = None,
    reason_code: str | None = None,
    valid: bool = False,
    guard_allowed: bool | None = None,
    error_code: str | None = None,
) -> ShadowRoutingResult:
    return ShadowRoutingResult(
        action=action,
        tool_name=tool_name,
        reason_code=reason_code,
        valid=valid,
        guard_allowed=guard_allowed,
        error_code=error_code,
        disagreement=_disagreement(
            legacy_action, legacy_tool, action, tool_name,
        ),
    )


def record_shadow_skip(
    *,
    legacy_action: str | None,
    legacy_tool: str | None,
    error_code: str,
) -> ShadowRoutingResult:
    """Record an eligible-path skip without making a shadow LLM call."""
    result = _result(
        legacy_action=legacy_action,
        legacy_tool=legacy_tool,
        error_code=error_code,
    )
    _emit_telemetry(
        result,
        legacy_action=legacy_action,
        legacy_tool=legacy_tool,
    )
    return result


def _parse_shadow_decision(raw: object) -> tuple[PlannerDecision | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, 'invalid_json'
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, 'invalid_json'
    if not isinstance(payload, dict):
        return None, 'schema_invalid'

    if (
        payload.get('action') == 'tool'
        and payload.get('tool_name') not in _TOOL_VALUES
    ):
        return None, 'unknown_tool'
    try:
        decision = PlannerDecision.model_validate(payload)
        decision.validate_decision()
    except (ValidationError, PlannerDecisionError, TypeError):
        return None, 'schema_invalid'
    return decision, None


def run_shadow_routing(
    *,
    question: str,
    authorized_tools: Sequence[str],
    tool_history: list[dict],
    observation: str,
    steps_left: int,
    memory_context: dict | None,
    execution_history: list[dict],
    context: DomainContext,
    guard_for_tool: Callable[[str], WorkflowGuard | None],
    legacy_action: str | None,
    legacy_tool: str | None,
    timeout_seconds: float,
) -> ShadowRoutingResult:
    """Make one non-executing Planner observation over authorized Tools only."""
    # Import lazily to keep planner_node -> shadow_routing acyclic while still
    # reusing the formal Planner prompt builders exactly.
    from app.agents.planner_node import build_planner_prompt, build_planner_system_prompt

    try:
        system_prompt = build_planner_system_prompt(list(authorized_tools))
        user_prompt = build_planner_prompt(
            question,
            list(authorized_tools),
            tool_history,
            observation,
            steps_left,
            memory_context,
            execution_history,
        )
        bounded_timeout = min(
            SHADOW_ROUTING_TIMEOUT_SECONDS,
            max(0.1, float(timeout_seconds)),
        )
        raw = call_llm(
            system_prompt,
            user_prompt,
            timeout_seconds=bounded_timeout,
            response_format={'type': 'json_object'},
            thinking=False,
        )
    except LLMProviderError as exc:
        result = _result(
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
            error_code=exc.code,
        )
        _emit_telemetry(
            result,
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
        )
        return result
    except Exception:
        result = _result(
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
            error_code='shadow_error',
        )
        _emit_telemetry(
            result,
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
        )
        return result

    decision, error_code = _parse_shadow_decision(raw)
    if decision is None:
        result = _result(
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
            error_code=error_code or 'schema_invalid',
        )
        _emit_telemetry(
            result,
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
        )
        return result

    action = decision.action
    tool_name = decision.tool_name
    reason_code = decision.reason_code
    if action == 'tool' and tool_name not in authorized_tools:
        result = _result(
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
            action=action,
            tool_name=tool_name,
            reason_code=reason_code,
            error_code='tool_not_authorized',
        )
        _emit_telemetry(
            result,
            legacy_action=legacy_action,
            legacy_tool=legacy_tool,
        )
        return result

    guard_allowed: bool | None = None
    guard_error: str | None = None
    if action == 'tool' and tool_name is not None:
        try:
            guard = guard_for_tool(tool_name)
        except Exception:
            guard = None
            guard_error = 'guard_error'
        if guard is not None:
            try:
                guard.validate_tool_call(
                    tool_name,
                    dict(decision.arguments or {}),
                    context,
                )
                guard_allowed = True
            except DomainToolCallRejected:
                guard_allowed = False
                guard_error = 'guard_rejected'
            except Exception:
                guard_error = 'guard_error'

    result = _result(
        legacy_action=legacy_action,
        legacy_tool=legacy_tool,
        action=action,
        tool_name=tool_name,
        reason_code=reason_code,
        valid=True,
        guard_allowed=guard_allowed,
        error_code=guard_error,
    )
    _emit_telemetry(
        result,
        legacy_action=legacy_action,
        legacy_tool=legacy_tool,
    )
    return result
