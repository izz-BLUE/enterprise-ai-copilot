"""真实 PostgreSQL 的 Durable HITL user-confirmation 验收。"""

import hashlib
import json
import os
from datetime import date
from unittest.mock import patch

import pytest

from app.agents.langgraph_agent import (
    compile_agent_loop_graph,
    finalize_node,
    resume_hitl_langgraph_agent,
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.runtime.checkpoint_runtime import CheckpointRuntime
from app.runtime.execution_recovery import (
    RecoveryMode,
    inspect_hitl_resume,
    inspect_recovery,
)
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker

_DSN = os.getenv('LANGGRAPH_CHECKPOINT_DSN', '')
pytestmark = pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_CHECKPOINT_INTEGRATION') != 'true' or not _DSN,
    reason='PostgreSQL HITL integration requires LANGGRAPH_CHECKPOINT_DSN',
)


def _thread_id(label: str) -> str:
    return 'rt_' + hashlib.sha256(label.encode()).hexdigest() + ':planner-v1'


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _tool_call():
    return json.dumps({
        'action': 'tool',
        'tool_name': 'leave_proposal_tool',
        'arguments': {},
        'reason_code': 'need_proposal',
    })


def _finish():
    return json.dumps({
        'action': 'finish',
        'answer': '请确认这份年假申请。',
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _proposal_result():
    return json.dumps({
        'kind': 'proposal',
        'action_proposal': {
            'action_type': 'ANNUAL_LEAVE_REQUEST',
            'start_date': '2026-09-01',
            'end_date': '2026-09-01',
            'reason': '私事',
            'half_day': 'NONE',
        },
        'missing_fields': [],
    }, ensure_ascii=False)


def _runtime() -> CheckpointRuntime:
    runtime = CheckpointRuntime(
        dsn=_DSN, connect_timeout_seconds=3, max_connections=3,
    )
    runtime.start()
    return runtime


def _run_to_wait(graph, thread_id: str):
    with patch('app.agents.planner_node.call_llm', side_effect=[_tool_call(), _finish()]), \
            patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
        tool.invoke.return_value = _proposal_result()
        result = run_langgraph_agent(
            '申请 2026-09-01 年假，原因私事',
            use_planner=True,
            allow_business_actions=True,
            business_date=date(2026, 8, 27),
            employee_id='E10001',
            graph=graph,
            runtime_thread_id=thread_id,
        )
    snapshot = graph.get_state(_config(thread_id))
    wait = HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])
    assert '__interrupt__' in result
    assert snapshot.next == ('approval_node',)
    assert len(snapshot.interrupts) == 1
    assert wait == HitlWaitMarker.for_execution(wait.execution_id, wait.action_type)
    return wait, snapshot


def _payload(wait: HitlWaitMarker, decision: str = 'CONFIRMED') -> HitlResumePayload:
    return HitlResumePayload(
        schema_version=1,
        wait_id=wait.wait_id,
        execution_id=wait.execution_id,
        decision=decision,
        action_id='act-java-001' if decision == 'CONFIRMED' else None,
        action_type=wait.action_type,
        action_status={
            'CONFIRMED': 'SUCCEEDED',
            'CANCELLED': 'CANCELLED',
            'EXPIRED': 'EXPIRED',
            'REJECTED': 'FAILED',
        }[decision],
        request_id='LR-202608-0001' if decision == 'CONFIRMED' else None,
        message='Java authoritative result',
    )


def test_hitl_wait_survives_restart_confirm_cancel_and_completed_replay():
    thread_id = _thread_id('p3-4-confirm-restart')
    runtime_a = _runtime()
    try:
        wait, before = _run_to_wait(runtime_a.get_graph(use_planner=True), thread_id)
        marker = before.values['execution_recovery']['execution_id']
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        waiting = inspect_recovery(
            graph.get_state(_config(thread_id)),
            question='用户重启后输入的新表述',
            business_date=date(2099, 1, 1),
            employee_id='E10001',
            allow_eval=False,
            allow_business_actions=False,
        )
        assert waiting.mode is RecoveryMode.WAITING_USER
        assert waiting.execution_id == marker

        payload = _payload(wait)
        assert inspect_hitl_resume(
            graph.get_state(_config(thread_id)), payload,
            employee_id='E10001', allow_business_actions=False,
        ).mode is RecoveryMode.WAITING_USER
        result = resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            payload=payload,
            allow_business_actions=False,
            business_date=date(2026, 8, 28),
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'hitl_confirmed'
        assert result['hitl_result']['action_status'] == 'SUCCEEDED'
        assert result['action_proposal'] is None
        assert graph.get_state(_config(thread_id)).next == ()
    finally:
        runtime_b.shutdown()

    runtime_c = _runtime()
    try:
        graph = runtime_c.get_graph(use_planner=True)
        before_replay = graph.get_state(_config(thread_id))
        replay = inspect_hitl_resume(
            before_replay, payload, employee_id='E10001', allow_business_actions=False,
        )
        assert replay.mode is RecoveryMode.HITL_COMPLETED
        assert graph.get_state(_config(thread_id)).values == before_replay.values
    finally:
        runtime_c.shutdown()

    # Keep this assertion outside the runtime lifecycle so a refactor cannot
    # accidentally lose the immutable execution correlation during replay.
    assert payload.execution_id == marker


def test_cancel_is_authoritative_and_does_not_reenter_planner_or_tools():
    thread_id = _thread_id('p3-4-cancel')
    runtime = _runtime()
    try:
        wait, _ = _run_to_wait(runtime.get_graph(use_planner=True), thread_id)
        graph = runtime.get_graph(use_planner=True)
        result = resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            payload=_payload(wait, 'CANCELLED'),
            allow_business_actions=True,
            business_date=date(2026, 8, 27),
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'hitl_cancelled'
        assert result['hitl_result']['action_status'] == 'CANCELLED'
        assert graph.get_state(_config(thread_id)).next == ()
    finally:
        runtime.shutdown()


def test_rejected_resume_is_durable_and_repeated_payload_is_noop():
    thread_id = _thread_id('p3-4-rejected')
    runtime = _runtime()
    try:
        wait, _ = _run_to_wait(runtime.get_graph(use_planner=True), thread_id)
        graph = runtime.get_graph(use_planner=True)
        payload = _payload(wait, 'REJECTED')
        assert inspect_hitl_resume(
            graph.get_state(_config(thread_id)), payload,
            employee_id='E10001', allow_business_actions=False,
        ).mode is RecoveryMode.WAITING_USER
        result = resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            payload=payload,
            allow_business_actions=False,
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'hitl_rejected'
        assert result['hitl_result']['action_id'] is None
        assert graph.get_state(_config(thread_id)).next == ()
        assert inspect_hitl_resume(
            graph.get_state(_config(thread_id)), payload,
            employee_id='E10001', allow_business_actions=False,
        ).mode is RecoveryMode.HITL_COMPLETED
    finally:
        runtime.shutdown()


def test_wrong_actor_and_correlation_fail_closed_without_checkpoint_mutation():
    thread_id = _thread_id('p3-4-fail-closed')
    runtime = _runtime()
    try:
        wait, before = _run_to_wait(runtime.get_graph(use_planner=True), thread_id)
        graph = runtime.get_graph(use_planner=True)
        actor_conflict = inspect_recovery(
            before,
            question='任意问题',
            business_date=date(2099, 1, 1),
            employee_id='E20002',
            allow_eval=False,
            allow_business_actions=True,
        )
        assert actor_conflict.mode is RecoveryMode.CONFLICT_ACTOR_SCOPE

        wrong_payload = _payload(
            wait,
        ).model_copy(update={'wait_id': 'wait_' + '0' * 64})
        correlation_conflict = inspect_hitl_resume(
            before, wrong_payload, employee_id='E10001', allow_business_actions=True,
        )
        assert correlation_conflict.mode is RecoveryMode.UNSAFE_REPLAY
        after = graph.get_state(_config(thread_id))
        assert after.next == before.next == ('approval_node',)
        assert after.values == before.values
    finally:
        runtime.shutdown()


def test_crash_after_approval_is_resumed_from_finalizer_checkpoint():
    thread_id = _thread_id('p3-4-finalize-crash')
    runtime_a = _runtime()
    try:
        calls = {'finalize': 0}

        def crash_once(state):
            calls['finalize'] += 1
            if calls['finalize'] == 1:
                raise RuntimeError('simulated crash after HITL approval')
            return finalize_node(state)

        with patch('app.agents.langgraph_agent.finalize_node', side_effect=crash_once):
            crash_graph = compile_agent_loop_graph(checkpointer=runtime_a._saver)
        wait, _ = _run_to_wait(crash_graph, thread_id)
        payload = _payload(wait, 'EXPIRED').model_copy(update={
            'message': '该申请草稿已过期，请重新生成。',
        })
        with pytest.raises(RuntimeError, match='simulated crash after HITL approval'):
            resume_hitl_langgraph_agent(
                graph=crash_graph,
                runtime_thread_id=thread_id,
                payload=payload,
                allow_business_actions=False,
                business_date=date(2026, 8, 27),
                employee_id='E10001',
            )
        crashed = crash_graph.get_state(_config(thread_id))
        assert crashed.next == ('finalize_node',)
        assert crashed.values['hitl_result']['decision'] == 'EXPIRED'
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        decision = inspect_hitl_resume(
            graph.get_state(_config(thread_id)), payload,
            employee_id='E10001', allow_business_actions=False,
        )
        assert decision.mode is RecoveryMode.HITL_CONTINUATION
        result = resume_langgraph_agent(
            graph=graph,
            runtime_thread_id=thread_id,
            allow_business_actions=False,
            business_date=date(2026, 8, 27),
            employee_id='E10001',
        )
        assert result['stop_reason'] == 'hitl_expired'
        assert result['hitl_result'] == payload.model_dump()
        assert graph.get_state(_config(thread_id)).next == ()
    finally:
        runtime_b.shutdown()
