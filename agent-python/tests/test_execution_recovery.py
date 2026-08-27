import json
from datetime import date
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.agents.tool_executor_node import _TOOL_REGISTRY, ToolSpec, is_tool_resume_safe
from app.runtime.execution_recovery import RecoveryMode, inspect_recovery
from app.schemas.execution_recovery_schema import (
    ExecutionRecoveryMarker,
    fingerprint_actor_scope,
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


def _values(
    question='原始问题',
    business_date=date(2026, 8, 27),
    employee_id='E10001',
):
    return {
        'question': question,
        'tool_history': [],
        'action_proposal': None,
        'execution_recovery': new_execution_recovery_marker(
            question, business_date, employee_id,
        ),
    }


def _inspect(
    snapshot,
    *,
    question='原始问题',
    business_date=date(2026, 8, 27),
    employee_id='E10001',
    allow_eval=False,
    allow_business_actions=False,
):
    return inspect_recovery(
        snapshot,
        question=question,
        business_date=business_date,
        employee_id=employee_id,
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
    )


def _invoice_decision():
    return {
        'action': 'tool',
        'tool_name': 'invoice_verify_tool',
        'arguments': {'invoice_id': 'INV-001'},
        'reason_code': 'need_invoice_verify',
    }


def test_marker_is_strict_and_fingerprint_preserves_exact_question():
    marker = new_execution_recovery_marker('继续报销', date(2026, 8, 27), 'E10001')
    parsed = ExecutionRecoveryMarker.model_validate(marker)

    assert parsed.execution_id.startswith('ex_')
    assert parsed.execution_date_anchor == '2026-08-27'
    assert fingerprint_request('继续报销') != fingerprint_request('继续报销 ')
    assert fingerprint_actor_scope('E10001') != fingerprint_actor_scope('E20002')
    assert fingerprint_actor_scope('') == sha256(
        b'enterprise-ai-copilot:execution-actor:v1\0'
    ).hexdigest()
    with pytest.raises(Exception):
        ExecutionRecoveryMarker.model_validate({**marker, 'trace_id': 'must-reject'})


def test_completed_or_missing_snapshot_always_starts_fresh():
    assert _inspect(
        None, question='Q', business_date=None,
    ).mode is RecoveryMode.NEW_EXECUTION
    assert _inspect(
        _snapshot((), values={}), question='Q', business_date=None,
    ).mode is RecoveryMode.NEW_EXECUTION


def test_incomplete_checkpoint_requires_marker_and_exact_request_and_date():
    assert _inspect(
        _snapshot(('planner_node',), values={}),
        question='Q', business_date=None,
    ).mode is RecoveryMode.INCOMPATIBLE_CHECKPOINT

    request_conflict = _inspect(
        _snapshot(('planner_node',), values=_values('A', None)),
        question='B', business_date=None,
    )
    assert request_conflict.mode is RecoveryMode.CONFLICT_REQUEST

    date_conflict = _inspect(
        _snapshot(('planner_node',), values=_values('A', date(2026, 8, 27))),
        question='A', business_date=date(2026, 8, 28),
    )
    assert date_conflict.mode is RecoveryMode.CONFLICT_DATE

    inconsistent_state = _values('different persisted question', None)
    inconsistent_state['execution_recovery'] = new_execution_recovery_marker(
        'A', None, 'E10001',
    )
    assert _inspect(
        _snapshot(('planner_node',), values=inconsistent_state),
        question='A', business_date=None,
    ).mode is RecoveryMode.CONFLICT_REQUEST


def test_interrupts_multiple_or_unknown_pending_nodes_fail_closed():
    values = _values()
    assert _inspect(
        _snapshot(('planner_node',), values=values, interrupts=('interrupt',)),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSUPPORTED_INTERRUPT
    assert _inspect(
        _snapshot(('planner_node',), values=values,
                  tasks=(SimpleNamespace(interrupts=('interrupt',)),)),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSUPPORTED_INTERRUPT
    assert _inspect(
        _snapshot(('planner_node', 'finalize_node'), values=values),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSAFE_REPLAY
    assert _inspect(
        _snapshot(('future_node',), values=values),
        question='原始问题', business_date=date(2026, 8, 27),
    ).mode is RecoveryMode.UNSAFE_REPLAY


def test_tool_executor_pending_requires_valid_explicitly_safe_tool():
    values = _values()
    values['planner_decision'] = _invoice_decision()
    decision = _inspect(
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
        assert _inspect(
            _snapshot(('tool_executor_node',), values=values),
            question='原始问题', business_date=date(2026, 8, 27),
        ).mode is RecoveryMode.UNSAFE_REPLAY


def test_actor_scope_binding_requires_same_employee_and_never_persists_raw_scope():
    values = _values(employee_id='E10001')
    same_actor = _inspect(
        _snapshot(('planner_node',), values=values),
        employee_id='E10001',
    )
    changed_actor = _inspect(
        _snapshot(('planner_node',), values=values),
        employee_id='E20002',
    )

    assert same_actor.mode is RecoveryMode.RESUME
    assert changed_actor.mode is RecoveryMode.CONFLICT_ACTOR_SCOPE
    assert changed_actor.reason == 'actor_scope_changed'
    serialized = json.dumps(values['execution_recovery'], ensure_ascii=False)
    assert 'E10001' not in serialized
    assert 'employee_id' not in serialized
    assert 'user_id' not in serialized
    assert 'conversation_id' not in serialized
    assert 'allow_eval' not in serialized
    assert 'allow_business_actions' not in serialized
    assert 'trace_id' not in serialized
    assert 'deadline_monotonic' not in serialized
    assert len(values['execution_recovery']['actor_scope_fingerprint']) == 64


def test_capability_residue_gate_blocks_revoked_privileged_material():
    eval_values = _values()
    eval_values['tool_history'] = [{
        'tool_name': 'eval_report_tool',
        'status': 'success',
        'observation': 'privileged eval result',
    }]
    revoked_eval = _inspect(
        _snapshot(('planner_node',), values=eval_values),
        allow_eval=False,
    )
    valid_eval = _inspect(
        _snapshot(('planner_node',), values=eval_values),
        allow_eval=True,
    )

    assert revoked_eval.mode is RecoveryMode.CONFLICT_CAPABILITY
    assert revoked_eval.reason == 'eval_capability_revoked'
    assert valid_eval.mode is RecoveryMode.RESUME

    for tool_name in ('leave_proposal_tool', 'expense_proposal_tool'):
        proposal_values = _values()
        proposal_values['tool_history'] = [{
            'tool_name': tool_name,
            'status': 'success',
            'observation': 'proposal result',
        }]
        revoked_proposal = _inspect(
            _snapshot(('planner_node',), values=proposal_values),
            allow_business_actions=False,
        )
        assert revoked_proposal.mode is RecoveryMode.CONFLICT_CAPABILITY
        assert revoked_proposal.reason == 'business_capability_revoked'

    action_values = _values()
    action_values['action_proposal'] = {'action_type': 'EXPENSE_CLAIM'}
    assert _inspect(
        _snapshot(('planner_node',), values=action_values),
        allow_business_actions=False,
    ).mode is RecoveryMode.CONFLICT_CAPABILITY


def test_capability_change_before_sensitive_material_still_allows_resume():
    values = _values()
    values['tool_history'] = [{
        'tool_name': 'travel_record_tool',
        'status': 'success',
        'observation': 'read-only travel result',
    }]
    assert _inspect(
        _snapshot(('planner_node',), values=values),
        allow_eval=False,
        allow_business_actions=False,
    ).mode is RecoveryMode.RESUME


def test_pending_eval_without_success_residue_resumes_then_current_gate_denies():
    values = _values()
    values['planner_decision'] = {
        'action': 'tool',
        'tool_name': 'eval_report_tool',
        'arguments': {'report_type': 'all'},
        'reason_code': 'need_eval',
    }
    decision = _inspect(
        _snapshot(('tool_executor_node',), values=values),
        allow_eval=False,
    )
    assert decision.mode is RecoveryMode.RESUME
    assert not any(
        item.get('tool_name') == 'eval_report_tool'
        and item.get('status') == 'success'
        for item in values.get('tool_history', [])
    )


def test_replay_policy_defaults_false_and_current_registry_is_explicit():
    assert ToolSpec(name='synthetic', executable_ref='synthetic').resume_safe is False
    assert is_tool_resume_safe('unknown_tool') is False
    assert all(spec.resume_safe for spec in _TOOL_REGISTRY.values())
