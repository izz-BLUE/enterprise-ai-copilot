"""持久化外部报销审批恢复的真实 PostgreSQL 验收。"""

import hashlib
import json
import os
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.agents.langgraph_agent import (
    compile_agent_loop_graph,
    finalize_node,
    resume_external_langgraph_agent,
    resume_hitl_langgraph_agent,
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.runtime.checkpoint_runtime import CheckpointRuntime
from app.runtime.execution_recovery import (
    RecoveryMode,
    inspect_external_resume,
    inspect_hitl_resume,
    inspect_recovery,
)
from app.schemas.external_wait_schema import ExternalResumePayload, ExternalWaitMarker
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker

_DSN = os.getenv('LANGGRAPH_CHECKPOINT_DSN', '')
pytestmark = pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_CHECKPOINT_INTEGRATION') != 'true' or not _DSN,
    reason='PostgreSQL external resume integration requires LANGGRAPH_CHECKPOINT_DSN',
)


def _thread_id(label: str) -> str:
    unique = f'{label}-{uuid4().hex}'
    return 'rt_' + hashlib.sha256(unique.encode()).hexdigest() + ':planner-v1'


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _runtime() -> CheckpointRuntime:
    runtime = CheckpointRuntime(
        dsn=_DSN, connect_timeout_seconds=3, max_connections=3,
    )
    runtime.start()
    return runtime


def _tool_call() -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': 'expense_proposal_tool',
        'arguments': {},
        'reason_code': 'need_expense_proposal',
    })


def _finish() -> str:
    return json.dumps({
        'action': 'finish',
        'answer': '请确认报销申请。',
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _proposal_result() -> str:
    return json.dumps({
        'kind': 'proposal',
        'action_proposal': {
            'action_type': 'EXPENSE_CLAIM',
            'trip_id': 'TRIP-PG-001',
            'expense_items': [{
                'category': 'HOTEL',
                'amount': 800,
                'invoice_id': 'INV-PG-001',
                'description': '住宿',
            }],
            'claimed_amount': 800,
            'reimbursable_amount': 800,
            'cost_center': 'CC-PG',
            'reason': '客户拜访',
            'invoice_ids': ['INV-PG-001'],
            'stay_nights': 1,
        },
        'missing_fields': [],
        'message': '报销草稿已生成',
    }, ensure_ascii=False)


def _run_to_user_wait(graph, thread_id: str) -> HitlWaitMarker:
    with patch('app.agents.planner_node.call_llm', side_effect=[_tool_call(), _finish()]), \
            patch('app.agents.tool_executor_node.expense_proposal_tool') as tool:
        tool.invoke.return_value = _proposal_result()
        result = run_langgraph_agent(
            '提交差旅报销',
            use_planner=True,
            allow_business_actions=True,
            business_date=date(2026, 8, 27),
            employee_id='E10001',
            graph=graph,
            runtime_thread_id=thread_id,
        )
    snapshot = graph.get_state(_config(thread_id))
    assert '__interrupt__' in result
    assert snapshot.next == ('approval_node',)
    return HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])


def _hitl_payload(wait: HitlWaitMarker) -> HitlResumePayload:
    return HitlResumePayload(
        schema_version=1,
        wait_id=wait.wait_id,
        execution_id=wait.execution_id,
        decision='CONFIRMED',
        action_id='act-pg-expense-001',
        action_type='EXPENSE_CLAIM',
        action_status='SUCCEEDED',
        request_id='EXP-PG-20260827-0001',
        message='报销申请已提交。',
    )


def _run_to_external_wait(graph, thread_id: str):
    hitl_wait = _run_to_user_wait(graph, thread_id)
    hitl_payload = _hitl_payload(hitl_wait)
    result = resume_hitl_langgraph_agent(
        graph=graph,
        runtime_thread_id=thread_id,
        payload=hitl_payload,
        allow_eval=False,
        allow_business_actions=False,
        business_date=date(2026, 8, 28),
        employee_id='E10001',
    )
    snapshot = graph.get_state(_config(thread_id))
    wait = ExternalWaitMarker.model_validate(snapshot.values['external_wait'])
    assert '__interrupt__' in result
    assert snapshot.next == ('external_wait_node',)
    assert len(snapshot.interrupts) == 1
    return hitl_payload, wait, snapshot


def _external_payload(
    wait: ExternalWaitMarker,
    decision: str = 'APPROVED',
) -> ExternalResumePayload:
    return ExternalResumePayload(
        schema_version=1,
        wait_id=wait.wait_id,
        execution_id=wait.execution_id,
        action_type=wait.action_type,
        request_id=wait.request_id,
        decision=decision,
        status=decision,
        message='OA 审批已通过。' if decision == 'APPROVED' else 'OA 审批已拒绝。',
    )


def test_external_wait_survives_restart_then_approved_cross_date_and_completed_replay():
    thread_id = _thread_id('external-approved-restart')
    runtime_a = _runtime()
    try:
        graph_a = runtime_a.get_graph(use_planner=True)
        _, wait, waiting_snapshot = _run_to_external_wait(graph_a, thread_id)
        preserved = {
            'execution_id': waiting_snapshot.values['execution_recovery']['execution_id'],
            'step_count': waiting_snapshot.values['step_count'],
            'tool_call_count': waiting_snapshot.values['tool_call_count'],
            'tool_history': waiting_snapshot.values['tool_history'],
            'execution_history': waiting_snapshot.values['execution_history'],
        }
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph_b = runtime_b.get_graph(use_planner=True)
        snapshot = graph_b.get_state(_config(thread_id))
        waiting = inspect_recovery(
            snapshot,
            question='数天后的新问题不能覆盖等待状态',
            business_date=date(2099, 1, 1),
            employee_id='E10001',
            allow_eval=False,
            allow_business_actions=False,
        )
        assert waiting.mode is RecoveryMode.WAITING_EXTERNAL
        payload = _external_payload(wait)
        assert inspect_external_resume(
            snapshot, payload, employee_id='E10001',
        ).mode is RecoveryMode.WAITING_EXTERNAL
        result = resume_external_langgraph_agent(
            graph=graph_b,
            runtime_thread_id=thread_id,
            payload=payload,
            allow_eval=False,
            allow_business_actions=False,
            business_date=date(2099, 1, 1),
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'external_approved'
        assert graph_b.get_state(_config(thread_id)).next == ()
        assert result['execution_recovery']['execution_id'] == preserved['execution_id']
        assert result['step_count'] == preserved['step_count']
        assert result['tool_call_count'] == preserved['tool_call_count']
        assert result['tool_history'] == preserved['tool_history']
        assert result['execution_history'] == preserved['execution_history']
    finally:
        runtime_b.shutdown()

    runtime_c = _runtime()
    try:
        graph_c = runtime_c.get_graph(use_planner=True)
        before = graph_c.get_state(_config(thread_id))
        assert inspect_external_resume(
            before, payload, employee_id='E10001',
        ).mode is RecoveryMode.EXTERNAL_COMPLETED
        assert graph_c.get_state(_config(thread_id)).values == before.values
    finally:
        runtime_c.shutdown()


def test_rejected_external_resume_is_durable():
    thread_id = _thread_id('external-rejected')
    runtime = _runtime()
    try:
        graph = runtime.get_graph(use_planner=True)
        _, wait, _ = _run_to_external_wait(graph, thread_id)
        payload = _external_payload(wait, 'REJECTED')
        result = resume_external_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            payload=payload,
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'external_rejected'
        assert result['answer'] == 'OA 审批已拒绝。'
        assert graph.get_state(_config(thread_id)).next == ()
    finally:
        runtime.shutdown()


def test_wrong_correlation_and_actor_mismatch_leave_checkpoint_unchanged():
    thread_id = _thread_id('external-conflicts')
    runtime = _runtime()
    try:
        graph = runtime.get_graph(use_planner=True)
        _, wait, before = _run_to_external_wait(graph, thread_id)
        payload = _external_payload(wait)
        assert inspect_external_resume(
            before, payload, employee_id='E20002',
        ).mode is RecoveryMode.CONFLICT_ACTOR_SCOPE
        for update in (
            {'wait_id': 'extwait_' + '0' * 64},
            {'execution_id': 'ex_' + '0' * 32},
            {'request_id': 'EXP-WRONG'},
        ):
            wrong = payload.model_copy(update=update)
            assert inspect_external_resume(
                before, wrong, employee_id='E10001',
            ).mode is RecoveryMode.UNSAFE_REPLAY
        after = graph.get_state(_config(thread_id))
        assert after.values == before.values
        assert after.next == before.next == ('external_wait_node',)
    finally:
        runtime.shutdown()


def test_external_command_finalize_fault_continues_after_restart():
    thread_id = _thread_id('external-finalize-fault')
    runtime_a = _runtime()
    try:
        calls = {'finalize': 0}

        def crash_once(state):
            calls['finalize'] += 1
            if calls['finalize'] == 1:
                raise RuntimeError('simulated external finalizer fault')
            return finalize_node(state)

        with patch('app.agents.langgraph_agent.finalize_node', side_effect=crash_once):
            crash_graph = compile_agent_loop_graph(checkpointer=runtime_a._saver)
        _, wait, _ = _run_to_external_wait(crash_graph, thread_id)
        payload = _external_payload(wait)
        with pytest.raises(RuntimeError, match='simulated external finalizer fault'):
            resume_external_langgraph_agent(
                graph=crash_graph,
                runtime_thread_id=thread_id,
                payload=payload,
                employee_id='E10001',
            )
        crashed = crash_graph.get_state(_config(thread_id))
        assert crashed.next == ('finalize_node',)
        assert crashed.values['external_result'] == payload.model_dump()
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        decision = inspect_external_resume(
            graph.get_state(_config(thread_id)), payload, employee_id='E10001',
        )
        assert decision.mode is RecoveryMode.EXTERNAL_CONTINUATION
        result = resume_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            allow_eval=False,
            allow_business_actions=False,
            business_date=date(2099, 1, 1),
            employee_id='E10001',
        )
        assert result['external_result'] == payload.model_dump()
        assert graph.get_state(_config(thread_id)).next == ()
    finally:
        runtime_b.shutdown()


def test_repeated_confirmed_hitl_recovers_same_external_wait_without_third_interrupt():
    thread_id = _thread_id('hitl-response-loss')
    runtime_a = _runtime()
    try:
        graph = runtime_a.get_graph(use_planner=True)
        hitl_payload, wait, before = _run_to_external_wait(graph, thread_id)
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        snapshot = graph.get_state(_config(thread_id))
        decision = inspect_hitl_resume(
            snapshot,
            hitl_payload,
            employee_id='E10001',
            allow_business_actions=False,
        )
        assert decision.mode is RecoveryMode.WAITING_EXTERNAL
        assert decision.external_wait == wait.model_dump()
        after = graph.get_state(_config(thread_id))
        assert after.values == before.values
        assert after.next == ('external_wait_node',)
        assert len(after.interrupts) == 1
    finally:
        runtime_b.shutdown()
