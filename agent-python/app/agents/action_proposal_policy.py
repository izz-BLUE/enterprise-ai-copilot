"""Shared deterministic policy for a proposal that is ready for Java HITL."""

from typing import Any

from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
)

PROPOSAL_TOOL_NAMES = frozenset({
    LEAVE_PROPOSAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
})


def is_confirmable_action_proposal(state: dict[str, Any]) -> bool:
    """Return whether the final state contains a genuinely confirmable proposal.

    The predicate is shared by HITL routing and finalization.  Clarifications do
    not qualify because they deliberately have no action proposal.
    """
    if state.get('stop_reason') != 'task_complete':
        return False
    if state.get('action_proposal') is None:
        return False

    last_success: str | None = None
    for entry in state.get('tool_history', []) or []:
        if isinstance(entry, dict) and entry.get('status') == 'success':
            last_success = entry.get('tool_name')
    return last_success in PROPOSAL_TOOL_NAMES
