from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.tool_executor_node import _TOOL_REGISTRY, ToolSpec, is_tool_resume_safe
from app.runtime.execution_recovery import RecoveryMode, inspect_recovery
from app.schemas.execution_recovery_schema import (
    ExecutionRecoveryMarker,
    fingerprint_request,
    new_execution_recovery_marker,
)


def _snapshot(next_nodes, *, values=None, interrupts=(), tasks=()):
    return SimpleNamespace(
        next=next_nodes,
        values=values if values is not None else {},
        interrupts=interrupts,
        tasks=tasks,
    )


def _values(question='原始问题', business_date=date(2026, 8, 27)):
    return {
        'question': question,
        'execution_recovery': new_execution_recovery_marker(question, business_date),
    }


def _invoice_decision():
    return {
        'action': 'tool',
        'tool_name': 'invoice_verify_tool',
        'arguments': {'invoice_id': 'INV-001'},
        'reason_code': 'need_invoice_verify',
    }


def test_marker_is_strict_and_fingerprint_preserves_exact_question():
    marker = new_execution_recovery_marker('继续报销', date(2026, 8, 27))
    parsed = ExecutionRecoveryMarker.model_validate(marker)

    assert parsed.execution_id.startswith('ex_')
    assert parsed.execution_date_anchor == '2026-08-27'
    assert fingerprint_request('继续报销') != fingerprint_request('继续报销 ')
    with pytest.raises(Exception):
        ExecutionRecoveryMarker.model_validate({**marker, 'trace_id': 'must-reject'})


def test_completed_or_missing_snapshot_always_starts_fresh():
    assert inspect_recovery(None, question='Q', business_date=None).mode is RecoveryMode.NEW_EXECUTION
    assert inspect_recovery(
        _snapshot((), values={}), question='Q', business_date=None,
    ).mode is RecoveryMode.NEW_EXECUTION


def test_incomplete_checkpoint_requires_marker_and_exact_request_and_date():
    assert inspect_recovery(
        _snapshot(('planner_node',), values={}),
        question='Q', business_date=None,
    ).mode is RecoveryMode.INCOMPATIBLE_CHECKPOINT

    request_conflict = inspect_recovery(
        _snapshot(('planner_node',), values=_values('A', None)),
        question='B', business_date=None,
    )
    assert request_conflict.mode is RecoveryMode.CONFLICT_REQUEST

    date_conflict = inspect_recovery(
        _snapshot(('planner_node',), values=_values('A', date(2026, 8, 27))),
        question='A', business_date=date(2026, 8, 28),
    )
    assert date_conflict.mode is RecoveryMode.CONFLICT_DATE

    inconsistent_state = _values('different persisted question', None)
    inconsistent_state['execution_recovery'] = new_execution_recovery_marker('A', None)
    assert inspect_recovery(
        _snapshot(('planner_node',), values=inconsistent_state),
        question='A', business_date=None,
    ).mode is RecoveryMode.CONFLICT_REQUEST


def test_interrupts_multiple_or_unknown_pending_nodes_fail_closed():
    values = _values()
    assert inspect_recovery(
        _snapshot(('planner_node',), values=values, interrupts=('interrupt',)),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSUPPORTED_INTERRUPT
    assert inspect_recovery(
        _snapshot(('planner_node',), values=values,
                  tasks=(SimpleNamespace(interrupts=('interrupt',)),)),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSUPPORTED_INTERRUPT
    assert inspect_recovery(
        _snapshot(('planner_node', 'finalize_node'), values=values),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSAFE_REPLAY
    assert inspect_recovery(
        _snapshot(('future_node',), values=values),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSAFE_REPLAY


def test_tool_executor_pending_requires_valid_explicitly_safe_tool():
    values = _values()
    values['planner_decision'] = _invoice_decision()
    decision = inspect_recovery(
        _snapshot(('tool_executor_node',), values=values),
        question='原始问题', business_date=date(2026, 8, 27),
    )
    assert decision.mode is RecoveryMode.RESUME
    assert decision.pending_node == 'tool_executor_node'

    values['planner_decision'] = {
        **_invoice_decision(),
        'tool_name': 'rag_answer_tool',
        'arguments': {'question': 'Q'},
        'reason_code': 'need_knowledge',
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            'app.runtime.execution_recovery.is_tool_resume_safe',
            lambda _name: False,
        )
        assert inspect_recovery(
            _snapshot(('tool_executor_node',), values=values),
            question='原始问题', business_date=date(2026, 8, 27),
        ).mode is RecoveryMode.UNSAFE_REPLAY


def test_replay_policy_defaults_false_and_current_registry_is_explicit():
    assert ToolSpec(name='synthetic', executable_ref='synthetic').resume_safe is False
    assert is_tool_resume_safe('unknown_tool') is False
    assert all(spec.resume_safe for spec in _TOOL_REGISTRY.values())
