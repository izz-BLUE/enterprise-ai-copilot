"""Shared contracts and observation helpers for workflow guards."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from app.schemas.planner_schema import PlannerDecision, PlannerDecisionError


@dataclass(frozen=True)
class DomainContext:
    """Provider/Guard 可消费的当前请求视图，不包含 trusted Runtime Context。"""

    question: str
    tool_history: tuple[dict, ...] = field(default_factory=tuple)
    request_expense_reason: str | None = None
    action_proposal: object = None
    continuation_original_request: str | None = None
    continuation_leave_state: dict | None = None
    memory_context: object = None
    step_count: int = 0

    @classmethod
    def from_state(cls, state: dict) -> 'DomainContext':
        history = state.get('tool_history', [])
        return cls(
            question=state.get('question', ''),
            tool_history=tuple(history) if isinstance(history, list) else tuple(),
            request_expense_reason=state.get('request_expense_reason'),
            action_proposal=state.get('action_proposal'),
            continuation_original_request=state.get('continuation_original_request'),
            continuation_leave_state=state.get('continuation_leave_state'),
            memory_context=state.get('memory_context'),
            step_count=state.get('step_count', 0),
        )


class DomainToolCallRejected(PlannerDecisionError):
    """领域 second gate 拒绝一次非法 Tool 调用。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class WorkflowGuard(Protocol):
    domain_key: str
    tool_names: frozenset[str]

    def legal_tools(self, tools: Sequence[str], context: DomainContext, **kwargs: Any) -> list[str]: ...

    def terminal_clarification(self, context: DomainContext) -> str | None: ...

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext, **kwargs: Any
    ) -> None: ...

    def completion_contract(self, tools: Sequence[str]) -> str: ...

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext, **kwargs: Any
    ) -> None: ...

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None: ...

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext, **kwargs: Any
    ) -> tuple[PlannerDecision, dict[str, object]]: ...

    def is_completed_success(self, item: dict) -> bool: ...


def _successful_observations(tool_history: Sequence[dict], tool_name: str) -> list[dict]:
    result = []
    for item in tool_history:
        if item.get('tool_name') != tool_name or item.get('status') != 'success':
            continue
        try:
            payload = json.loads(item.get('observation'))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get('success', False):
            result.append(payload)
    return result


def _structured_observation_payload(item: dict) -> dict | None:
    observation = item.get('observation')
    if isinstance(observation, dict):
        return observation
    if not isinstance(observation, str):
        return None
    try:
        payload = json.loads(observation)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _tool_invocation_has_business_success(item: dict) -> bool:
    """把 Tool invocation success 与结构化业务 success 分开判断。"""
    if item.get('status') != 'success':
        return False
    payload = _structured_observation_payload(item)
    if isinstance(payload, dict) and 'success' in payload:
        return payload.get('success') is True
    return True


_STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE = (
    '最后一次 Tool 明确返回 business success=false，不能标记 task_complete；'
    '请根据当前能力和错误结果决定合理重试或 refuse/cannot_complete。'
)


def _latest_structured_tool_business_failure(
    tool_history: Sequence[dict],
) -> dict | None:
    """返回最新一次明确的结构化业务失败，兼容 legacy/malformed observation。"""
    for item in reversed(tool_history):
        if item.get('status') != 'success':
            continue
        payload = _structured_observation_payload(item)
        if isinstance(payload, dict) and 'success' in payload:
            return item if payload.get('success') is False else None
        return None
    return None
