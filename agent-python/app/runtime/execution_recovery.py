"""Deterministic eligibility checks for Planner-first crash resume."""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from pydantic import ValidationError

from app.agents.tool_executor_node import is_tool_resume_safe
from app.schemas.execution_recovery_schema import (
    ExecutionRecoveryMarker,
    fingerprint_request,
)
from app.schemas.planner_schema import PlannerDecision, PlannerDecisionError

_ALLOWED_PENDING_NODES = frozenset({
    'safety_node',
    'planner_node',
    'tool_executor_node',
    'finalize_node',
})


class RecoveryMode(str, Enum):
    NEW_EXECUTION = 'NEW'
    RESUME = 'RESUME'
    CONFLICT_REQUEST = 'CONFLICT_REQUEST'
    CONFLICT_DATE = 'CONFLICT_DATE'
    UNSUPPORTED_INTERRUPT = 'UNSUPPORTED_INTERRUPT'
    UNSAFE_REPLAY = 'UNSAFE_REPLAY'
    INCOMPATIBLE_CHECKPOINT = 'INCOMPATIBLE_CHECKPOINT'


@dataclass(frozen=True)
class RecoveryDecision:
    mode: RecoveryMode
    reason: str = ''
    pending_node: str | None = None
    execution_id: str | None = None

    @property
    def is_conflict(self) -> bool:
        return self.mode not in (RecoveryMode.NEW_EXECUTION, RecoveryMode.RESUME)


def _has_pending_interrupt(snapshot: Any) -> bool:
    if getattr(snapshot, 'interrupts', ()):
        return True
    for task in getattr(snapshot, 'tasks', ()) or ():
        if getattr(task, 'interrupts', ()):
            return True
    return False


def _current_date_anchor(business_date: date | None) -> str | None:
    return business_date.isoformat() if business_date else None


def inspect_recovery(
    snapshot: Any | None,
    *,
    question: str,
    business_date: date | None,
) -> RecoveryDecision:
    """Classify the latest head without mutating or scanning checkpoint history."""
    if snapshot is None:
        return RecoveryDecision(RecoveryMode.NEW_EXECUTION, reason='no_snapshot')

    next_nodes = tuple(getattr(snapshot, 'next', ()) or ())
    if not next_nodes:
        return RecoveryDecision(RecoveryMode.NEW_EXECUTION, reason='completed')

    if _has_pending_interrupt(snapshot):
        return RecoveryDecision(
            RecoveryMode.UNSUPPORTED_INTERRUPT,
            reason='interrupt_pending',
        )

    values = getattr(snapshot, 'values', None)
    if not isinstance(values, dict):
        return RecoveryDecision(
            RecoveryMode.INCOMPATIBLE_CHECKPOINT,
            reason='state_values_missing',
        )

    try:
        marker = ExecutionRecoveryMarker.model_validate(
            values.get('execution_recovery')
        )
    except (TypeError, ValidationError):
        return RecoveryDecision(
            RecoveryMode.INCOMPATIBLE_CHECKPOINT,
            reason='marker_invalid',
        )

    if (
        values.get('question') != question
        or marker.request_fingerprint != fingerprint_request(question)
    ):
        return RecoveryDecision(
            RecoveryMode.CONFLICT_REQUEST,
            reason='request_mismatch',
            execution_id=marker.execution_id,
        )

    if marker.execution_date_anchor != _current_date_anchor(business_date):
        return RecoveryDecision(
            RecoveryMode.CONFLICT_DATE,
            reason='date_changed',
            execution_id=marker.execution_id,
        )

    if len(next_nodes) != 1:
        return RecoveryDecision(
            RecoveryMode.UNSAFE_REPLAY,
            reason='multiple_pending_nodes',
            execution_id=marker.execution_id,
        )

    pending_node = next_nodes[0]
    if not isinstance(pending_node, str) or pending_node not in _ALLOWED_PENDING_NODES:
        return RecoveryDecision(
            RecoveryMode.UNSAFE_REPLAY,
            reason='unknown_pending_node',
            pending_node=pending_node,
            execution_id=marker.execution_id,
        )

    if pending_node == 'tool_executor_node':
        try:
            decision = PlannerDecision.model_validate(values.get('planner_decision'))
            decision.validate_decision()
        except (TypeError, ValidationError, PlannerDecisionError):
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='planner_decision_invalid',
                pending_node=pending_node,
                execution_id=marker.execution_id,
            )
        if decision.action != 'tool' or not is_tool_resume_safe(decision.tool_name):
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='tool_not_resume_safe',
                pending_node=pending_node,
                execution_id=marker.execution_id,
            )

    return RecoveryDecision(
        RecoveryMode.RESUME,
        reason='pending_checkpoint',
        pending_node=pending_node,
        execution_id=marker.execution_id,
    )
