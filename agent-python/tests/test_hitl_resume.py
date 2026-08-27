import json
from datetime import date
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.langgraph_agent import (
    compile_agent_loop_graph,
    resume_hitl_langgraph_agent,
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


def _wait_once(thread_id='hitl-test'):
    graph = compile_agent_loop_graph(checkpointer=InMemorySaver())
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
