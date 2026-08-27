"""Deterministic eligibility checks for Planner-first crash resume."""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from pydantic import ValidationError

from app.agents.tool_executor_node import is_tool_resume_safe
from app.schemas.execution_recovery_schema import (
    ExecutionRecoveryMarker,
    fingerprint_actor_scope,
    fingerprint_request,
)
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)

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
    CONFLICT_ACTOR_SCOPE = 'CONFLICT_ACTOR_SCOPE'
    CONFLICT_CAPABILITY = 'CONFLICT_CAPABILITY'
    UNSUPPORTED_INTERRUPT = 'UNSUPPORTED_INTERRUPT'
    UNSAFE_REPLAY = 'UNSAFE_REPLAY'
    INCOMPATIBLE_CHECKPOINT = 'INCOMPATIBLE_CHECKPOINT'
    WAITING_USER = 'WAITING_USER'
    HITL_CONTINUATION = 'HITL_CONTINUATION'
    HITL_COMPLETED = 'HITL_COMPLETED'


@dataclass(frozen=True)
class RecoveryDecision:
    mode: RecoveryMode
    reason: str = ''
    pending_node: str | None = None
    execution_id: str | None = None
    hitl_wait: dict | None = None

    @property
    def is_conflict(self) -> bool:
        return self.mode not in (
            RecoveryMode.NEW_EXECUTION,
            RecoveryMode.RESUME,
            RecoveryMode.WAITING_USER,
            RecoveryMode.HITL_CONTINUATION,
            RecoveryMode.HITL_COMPLETED,
        )


def _pending_interrupts(snapshot: Any) -> list[Any]:
    """Return unique interrupt objects; StateSnapshot exposes each twice."""
    found: list[Any] = []
    seen: set[str] = set()
    candidates = list(getattr(snapshot, 'interrupts', ()) or ())
    for task in getattr(snapshot, 'tasks', ()) or ():
        candidates.extend(getattr(task, 'interrupts', ()) or ())
    for item in candidates:
        item_id = getattr(item, 'id', None)
        key = str(item_id) if item_id is not None else repr(item)
        if key not in seen:
            seen.add(key)
            found.append(item)
    return found


def _has_pending_interrupt(snapshot: Any) -> bool:
    return bool(_pending_interrupts(snapshot))


def _current_date_anchor(business_date: date | None) -> str | None:
    return business_date.isoformat() if business_date else None


def _has_successful_tool(tool_history: Any, tool_names: frozenset[str]) -> bool:
    if not isinstance(tool_history, list):
        return False
    return any(
        isinstance(entry, dict)
        and entry.get('tool_name') in tool_names
        and entry.get('status') == 'success'
        for entry in tool_history
    )


def _capability_residue_reason(
    values: dict,
    *,
    allow_eval: bool,
    allow_business_actions: bool,
) -> str | None:
    """Reject persisted privileged material after its current capability is revoked."""
    tool_history = values.get('tool_history', [])
    if not allow_eval and _has_successful_tool(tool_history, frozenset({EVAL_TOOL_NAME})):
        return 'eval_capability_revoked'

    business_action_tools = frozenset({
        LEAVE_PROPOSAL_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
    })
    if not allow_business_actions and (
        _has_successful_tool(tool_history, business_action_tools)
        or values.get('action_proposal') is not None
    ):
        return 'business_capability_revoked'
    return None


def inspect_recovery(
    snapshot: Any | None,
    *,
    question: str,
    business_date: date | None,
    employee_id: str,
    allow_eval: bool,
    allow_business_actions: bool,
) -> RecoveryDecision:
    """Classify the latest head without mutating or scanning checkpoint history."""
    if snapshot is None:
        return RecoveryDecision(RecoveryMode.NEW_EXECUTION, reason='no_snapshot')

    next_nodes = tuple(getattr(snapshot, 'next', ()) or ())
    if not next_nodes:
        return RecoveryDecision(RecoveryMode.NEW_EXECUTION, reason='completed')

    values = getattr(snapshot, 'values', None)
    if not isinstance(values, dict):
        return RecoveryDecision(
            RecoveryMode.INCOMPATIBLE_CHECKPOINT,
            reason='state_values_missing',
        )

    if _has_pending_interrupt(snapshot):
        interrupts = _pending_interrupts(snapshot)
        if len(interrupts) != 1:
            return RecoveryDecision(
                RecoveryMode.UNSUPPORTED_INTERRUPT,
                reason='multiple_pending_interrupts',
            )
        try:
            marker = ExecutionRecoveryMarker.model_validate(
                values.get('execution_recovery'),
            )
            wait = HitlWaitMarker.model_validate(values.get('hitl_wait'))
            interrupt_wait = HitlWaitMarker.model_validate(
                getattr(interrupts[0], 'value', None),
            )
        except (TypeError, ValidationError):
            return RecoveryDecision(
                RecoveryMode.UNSUPPORTED_INTERRUPT,
                reason='hitl_wait_marker_invalid',
            )

        if (
            wait != interrupt_wait
            or wait.execution_id != marker.execution_id
            or not _is_confirmable_action_proposal(values)
        ):
            return RecoveryDecision(
                RecoveryMode.UNSUPPORTED_INTERRUPT,
                reason='hitl_wait_state_invalid',
                execution_id=marker.execution_id,
            )
        if marker.actor_scope_fingerprint != fingerprint_actor_scope(employee_id):
            return RecoveryDecision(
                RecoveryMode.CONFLICT_ACTOR_SCOPE,
                reason='actor_scope_changed',
                execution_id=marker.execution_id,
            )
        # A durable HITL wait is already at the approval boundary.  Returning
        # it to Java is side-effect free; Java still decides whether a new
        # PendingAction may be created.  In particular, a terminal Java action
        # may need to reconcile this wait after the current capability has
        # been revoked.  Do not apply the automatic Planner/Tool recovery
        # capability-residue gate to this approval-only checkpoint.
        return RecoveryDecision(
            RecoveryMode.WAITING_USER,
            reason='business_action_confirmation_pending',
            pending_node='approval_node',
            execution_id=marker.execution_id,
            hitl_wait=wait.model_dump(),
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

    if marker.actor_scope_fingerprint != fingerprint_actor_scope(employee_id):
        return RecoveryDecision(
            RecoveryMode.CONFLICT_ACTOR_SCOPE,
            reason='actor_scope_changed',
            execution_id=marker.execution_id,
        )

    capability_reason = _capability_residue_reason(
        values,
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
    )
    if capability_reason is not None:
        return RecoveryDecision(
            RecoveryMode.CONFLICT_CAPABILITY,
            reason=capability_reason,
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


def _is_confirmable_action_proposal(values: dict) -> bool:
    """Keep recovery independent from the graph module while sharing policy."""
    from app.agents.action_proposal_policy import is_confirmable_action_proposal

    return is_confirmable_action_proposal(values)


def inspect_hitl_resume(
    snapshot: Any | None,
    payload: HitlResumePayload,
    *,
    employee_id: str,
    allow_business_actions: bool,
) -> RecoveryDecision:
    """Validate an authoritative HITL resume against only the latest checkpoint."""
    if snapshot is None:
        return RecoveryDecision(RecoveryMode.INCOMPATIBLE_CHECKPOINT, reason='no_snapshot')
    values = getattr(snapshot, 'values', None)
    if not isinstance(values, dict):
        return RecoveryDecision(
            RecoveryMode.INCOMPATIBLE_CHECKPOINT,
            reason='state_values_missing',
        )
    try:
        marker = ExecutionRecoveryMarker.model_validate(values.get('execution_recovery'))
        wait = HitlWaitMarker.model_validate(values.get('hitl_wait'))
    except (TypeError, ValidationError):
        return RecoveryDecision(
            RecoveryMode.INCOMPATIBLE_CHECKPOINT,
            reason='hitl_marker_invalid',
        )

    if marker.actor_scope_fingerprint != fingerprint_actor_scope(employee_id):
        return RecoveryDecision(
            RecoveryMode.CONFLICT_ACTOR_SCOPE,
            reason='actor_scope_changed',
            execution_id=marker.execution_id,
        )
    # `allow_business_actions` is intentionally not a gate here.  The payload
    # is an authoritative terminal decision produced by Java after its own
    # feature/admin/identity/nonce/TTL/owner/idempotency checks.  The flag is
    # still re-injected into the Runtime Context by the caller, so any
    # accidental Planner/Tool re-entry remains capability-gated.
    if (
        payload.wait_id != wait.wait_id
        or payload.execution_id != marker.execution_id
        or payload.action_type != wait.action_type
    ):
        return RecoveryDecision(
            RecoveryMode.UNSAFE_REPLAY,
            reason='hitl_resume_correlation_mismatch',
            execution_id=marker.execution_id,
        )

    next_nodes = tuple(getattr(snapshot, 'next', ()) or ())
    if not next_nodes:
        try:
            completed = HitlResumePayload.model_validate(values.get('hitl_result'))
        except (TypeError, ValidationError):
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='completed_hitl_result_missing',
                execution_id=marker.execution_id,
            )
        if completed != payload:
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='completed_hitl_result_mismatch',
                execution_id=marker.execution_id,
            )
        return RecoveryDecision(
            RecoveryMode.HITL_COMPLETED,
            reason='completed',
            execution_id=marker.execution_id,
            hitl_wait=wait.model_dump(),
        )

    interrupts = _pending_interrupts(snapshot)
    if len(interrupts) == 1 and next_nodes == ('approval_node',):
        try:
            pending_wait = HitlWaitMarker.model_validate(
                getattr(interrupts[0], 'value', None),
            )
        except (TypeError, ValidationError):
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='pending_hitl_wait_invalid',
                execution_id=marker.execution_id,
            )
        if pending_wait != wait:
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='pending_hitl_wait_mismatch',
                execution_id=marker.execution_id,
            )
        return RecoveryDecision(
            RecoveryMode.WAITING_USER,
            reason='business_action_confirmation_pending',
            pending_node='approval_node',
            execution_id=marker.execution_id,
            hitl_wait=wait.model_dump(),
        )

    if next_nodes == ('finalize_node',):
        try:
            result = HitlResumePayload.model_validate(values.get('hitl_result'))
        except (TypeError, ValidationError):
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='hitl_continuation_result_invalid',
                execution_id=marker.execution_id,
            )
        if result != payload:
            return RecoveryDecision(
                RecoveryMode.UNSAFE_REPLAY,
                reason='hitl_continuation_result_mismatch',
                execution_id=marker.execution_id,
            )
        return RecoveryDecision(
            RecoveryMode.HITL_CONTINUATION,
            reason='finalization_pending',
            pending_node='finalize_node',
            execution_id=marker.execution_id,
            hitl_wait=wait.model_dump(),
        )

    return RecoveryDecision(
        RecoveryMode.UNSAFE_REPLAY,
        reason='unknown_hitl_pending_node',
        execution_id=marker.execution_id,
    )
