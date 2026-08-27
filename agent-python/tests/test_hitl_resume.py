import json
from datetime import date
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.langgraph_agent import (
    compile_agent_loop_graph,
    resume_hitl_langgraph_agent,
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.runtime.execution_recovery import RecoveryMode, inspect_hitl_resume, inspect_recovery
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker


def _tool_call(tool_name='leave_proposal_tool'):
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': {},
        'reason_code': 'need_proposal',
    })


def _finish():
    return json.dumps({
        'action': 'finish',
        'answer': '我已生成一份模拟申请草稿，请确认后提交。',
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
        'message': '草稿已生成',
    }, ensure_ascii=False)


def _wait_once(thread_id='hitl-test', graph=None):
    graph = graph or compile_agent_loop_graph(checkpointer=InMemorySaver())
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
    return graph, result


def _payload(wait, *, decision='CONFIRMED', wait_id=None, execution_id=None):
    return HitlResumePayload(
        schema_version=1,
        wait_id=wait_id or wait.wait_id,
        execution_id=execution_id or wait.execution_id,
        decision=decision,
        action_id='act_test' if decision == 'CONFIRMED' else None,
        action_type=wait.action_type,
        action_status={
            'CONFIRMED': 'SUCCEEDED',
            'CANCELLED': 'CANCELLED',
            'EXPIRED': 'EXPIRED',
            'REJECTED': 'FAILED',
        }[decision],
        request_id='LR-test' if decision == 'CONFIRMED' else None,
        message='Java authoritative result',
    )


def test_proposal_persists_one_wait_and_command_resume_finalizes_without_new_execution():
    graph, first = _wait_once()
    snapshot = graph.get_state({'configurable': {'thread_id': 'hitl-test'}})
    wait = HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])

    assert '__interrupt__' in first
    assert snapshot.next == ('approval_node',)
    assert snapshot.values['action_proposal'] is not None
    assert snapshot.values['hitl_wait'] == wait.model_dump()
    assert wait == HitlWaitMarker.for_execution(wait.execution_id, wait.action_type)
    assert not {'employee_id', 'user_id', 'conversation_id', 'trace_id', 'nonce'}.intersection(
        snapshot.values['hitl_wait'],
    )

    payload = _payload(wait)
    result = resume_hitl_langgraph_agent(
        graph=graph,
        runtime_thread_id='hitl-test',
        payload=payload,
        allow_business_actions=True,
        business_date=date(2026, 8, 28),
        employee_id='E10001',
    )

    assert result['stop_reason'] == 'hitl_confirmed'
    assert result['hitl_result']['decision'] == 'CONFIRMED'
    assert result['action_proposal'] is None
    assert result['execution_recovery']['execution_id'] == wait.execution_id
    assert graph.get_state({'configurable': {'thread_id': 'hitl-test'}}).next == ()


def test_wait_detection_ignores_question_and_date_but_rejects_actor_change():
    graph, _ = _wait_once('hitl-recovery')
    snapshot = graph.get_state({'configurable': {'thread_id': 'hitl-recovery'}})

    waiting = inspect_recovery(
        snapshot,
        question='第二天的新问题',
        business_date=date(2026, 9, 2),
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=True,
    )
    assert waiting.mode is RecoveryMode.WAITING_USER
    assert waiting.pending_node == 'approval_node'

    changed_actor = inspect_recovery(
        snapshot,
        question='任意问题',
        business_date=date(2099, 1, 1),
        employee_id='E20002',
        allow_eval=False,
        allow_business_actions=True,
    )
    assert changed_actor.mode is RecoveryMode.CONFLICT_ACTOR_SCOPE
    assert snapshot.next == ('approval_node',)


def test_waiting_hitl_recovery_is_returned_after_capability_revocation():
    graph, _ = _wait_once('hitl-wait-revoked')
    snapshot = graph.get_state({'configurable': {'thread_id': 'hitl-wait-revoked'}})

    decision = inspect_recovery(
        snapshot,
        question='新的输入不会覆盖原申请',
        business_date=date(2099, 1, 1),
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=False,
    )

    assert decision.mode is RecoveryMode.WAITING_USER
    assert decision.pending_node == 'approval_node'
    assert graph.get_state({'configurable': {'thread_id': 'hitl-wait-revoked'}}).next == (
        'approval_node',
    )


def test_wrong_wait_or_execution_fails_closed_without_checkpoint_mutation():
    graph, _ = _wait_once('hitl-wrong')
    config = {'configurable': {'thread_id': 'hitl-wrong'}}
    snapshot = graph.get_state(config)
    wait = HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])
    wrong = _payload(wait, wait_id='wait_' + '0' * 64)

    decision = inspect_hitl_resume(
        snapshot, wrong, employee_id='E10001', allow_business_actions=True,
    )
    assert decision.mode is RecoveryMode.UNSAFE_REPLAY
    assert graph.get_state(config).values == snapshot.values
    assert graph.get_state(config).next == ('approval_node',)


def test_terminal_resume_works_after_capability_revocation_without_reentering_planner_or_tools():
    graph, _ = _wait_once('hitl-revoked')
    snapshot = graph.get_state({'configurable': {'thread_id': 'hitl-revoked'}})
    wait = HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])
    payload = _payload(wait)

    assert inspect_hitl_resume(
        snapshot, payload, employee_id='E10001', allow_business_actions=False,
    ).mode is RecoveryMode.WAITING_USER

    with patch('app.agents.planner_node.call_llm', side_effect=AssertionError('planner re-entered')), \
            patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
        result = resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id='hitl-revoked',
            payload=payload,
            allow_business_actions=False,
            business_date=date(2099, 1, 1),
            employee_id='E10001',
        )

    assert result['stop_reason'] == 'hitl_confirmed'
    assert graph.get_state({'configurable': {'thread_id': 'hitl-revoked'}}).next == ()
    tool.invoke.assert_not_called()


def test_rejected_resume_is_terminal_and_repeated_payload_is_canonical():
    graph, _ = _wait_once('hitl-rejected')
    config = {'configurable': {'thread_id': 'hitl-rejected'}}
    wait = HitlWaitMarker.model_validate(graph.get_state(config).values['hitl_wait'])
    payload = _payload(wait, decision='REJECTED')

    with patch('app.agents.planner_node.call_llm', side_effect=AssertionError('planner re-entered')), \
            patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
        result = resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id='hitl-rejected',
            payload=payload,
            allow_business_actions=False,
            employee_id='E10001',
        )

    assert result['stop_reason'] == 'hitl_rejected'
    assert result['hitl_result']['action_id'] is None
    assert result['hitl_result']['action_status'] == 'FAILED'
    assert graph.get_state(config).next == ()
    completed = inspect_hitl_resume(
        graph.get_state(config), payload,
        employee_id='E10001', allow_business_actions=False,
    )
    assert completed.mode is RecoveryMode.HITL_COMPLETED
    tool.invoke.assert_not_called()


def test_expired_resume_replays_from_finalize_checkpoint_with_canonical_message():
    calls = {'finalize': 0}

    def crash_once(state):
        calls['finalize'] += 1
        if calls['finalize'] == 1:
            raise RuntimeError('simulated expired finalizer crash')
        return state

    with patch('app.agents.langgraph_agent.finalize_node', side_effect=crash_once):
        graph = compile_agent_loop_graph(checkpointer=InMemorySaver())
        graph, _ = _wait_once('hitl-expired', graph)
        wait = HitlWaitMarker.model_validate(graph.get_state(
            {'configurable': {'thread_id': 'hitl-expired'}},
        ).values['hitl_wait'])
        payload = _payload(wait, decision='EXPIRED').model_copy(update={
            'message': '该申请草稿已过期，请重新生成。',
        })
        with pytest.raises(RuntimeError, match='simulated expired finalizer crash'):
            resume_hitl_langgraph_agent(
                graph=graph,
                runtime_thread_id='hitl-expired',
                payload=payload,
                allow_business_actions=False,
                employee_id='E10001',
            )

    crashed = graph.get_state({'configurable': {'thread_id': 'hitl-expired'}})
    assert crashed.next == ('finalize_node',)
    assert inspect_hitl_resume(
        crashed, payload, employee_id='E10001', allow_business_actions=False,
    ).mode is RecoveryMode.HITL_CONTINUATION
    result = resume_langgraph_agent(
        graph=graph,
        runtime_thread_id='hitl-expired',
        allow_business_actions=False,
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'hitl_expired'
    assert result['hitl_result'] == payload.model_dump()
    assert graph.get_state({'configurable': {'thread_id': 'hitl-expired'}}).next == ()


def test_clarification_does_not_interrupt():
    graph = compile_agent_loop_graph(checkpointer=InMemorySaver())
    clarification = json.dumps({
        'kind': 'clarification',
        'action_proposal': None,
        'missing_fields': ['reason'],
        'message': '请补充原因',
    })
    with patch('app.agents.planner_node.call_llm', side_effect=[_tool_call(), _finish()]), \
            patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
        tool.invoke.return_value = clarification
        result = run_langgraph_agent(
            '申请年假', use_planner=True, allow_business_actions=True,
            business_date=date(2026, 8, 27), employee_id='E10001',
            graph=graph, runtime_thread_id='hitl-clarification',
        )
    assert result['action_proposal'] is None
    assert result['missing_fields'] == ['reason']
    assert graph.get_state({'configurable': {'thread_id': 'hitl-clarification'}}).next == ()
    assert 'hitl_wait' not in result or result['hitl_wait'] is None
