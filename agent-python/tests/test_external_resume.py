"""P3-5A durable external expense approval semantics."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.agents.langgraph_agent import (
    compile_agent_loop_graph,
    resume_external_langgraph_agent,
    resume_hitl_langgraph_agent,
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.runtime.execution_recovery import (
    RecoveryMode,
    inspect_external_resume,
    inspect_hitl_resume,
    inspect_recovery,
)
from app.schemas.external_wait_schema import ExternalResumePayload, ExternalWaitMarker
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _tool_call(tool_name: str) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': {},
        'reason_code': (
            'need_expense_proposal'
            if tool_name == 'expense_proposal_tool'
            else 'need_proposal'
        ),
    })


def _finish() -> str:
    return json.dumps({
        'action': 'finish',
        'answer': '请确认申请。',
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _expense_proposal() -> str:
    return json.dumps({
        'kind': 'proposal',
        'action_proposal': {
            'action_type': 'EXPENSE_CLAIM',
            'trip_id': 'TRIP-001',
            'expense_items': [{
                'category': 'HOTEL',
                'amount': 800,
                'invoice_id': 'INV-001',
                'description': '住宿',
            }],
            'claimed_amount': 800,
            'reimbursable_amount': 800,
            'cost_center': 'CC-001',
            'reason': '客户拜访',
            'invoice_ids': ['INV-001'],
            'stay_nights': 1,
        },
        'missing_fields': [],
        'message': '报销草稿已生成',
    }, ensure_ascii=False)


def _leave_proposal() -> str:
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
        'message': '年假草稿已生成',
    }, ensure_ascii=False)


def _run_to_user_wait(thread_id: str, *, expense: bool = True, graph=None):
    graph = graph or compile_agent_loop_graph(checkpointer=InMemorySaver())
    tool_name = 'expense_proposal_tool' if expense else 'leave_proposal_tool'
    target = (
        'app.agents.tool_executor_node.expense_proposal_tool'
        if expense else 'app.agents.tool_executor_node.leave_proposal_tool'
    )
    with patch('app.agents.planner_node.call_llm', side_effect=[
        _tool_call(tool_name), _finish(),
    ]), patch(target) as tool:
        tool.invoke.return_value = _expense_proposal() if expense else _leave_proposal()
        result = run_langgraph_agent(
            '提交报销' if expense else '申请年假',
            use_planner=True,
            allow_business_actions=True,
            business_date=date(2026, 8, 27),
            employee_id='E10001',
            graph=graph,
            runtime_thread_id=thread_id,
        )
    snapshot = graph.get_state(_config(thread_id))
    return graph, result, HitlWaitMarker.model_validate(snapshot.values['hitl_wait'])


def _hitl_payload(
    wait: HitlWaitMarker,
    *,
    decision: str = 'CONFIRMED',
    request_id: str | None = 'EXP-20260827-0001',
) -> HitlResumePayload:
    return HitlResumePayload(
        schema_version=1,
        wait_id=wait.wait_id,
        execution_id=wait.execution_id,
        decision=decision,
        action_id='act-expense-001' if decision == 'CONFIRMED' else None,
        action_type=wait.action_type,
        action_status={
            'CONFIRMED': 'SUCCEEDED',
            'CANCELLED': 'CANCELLED',
            'EXPIRED': 'EXPIRED',
            'REJECTED': 'FAILED',
        }[decision],
        request_id=request_id if decision == 'CONFIRMED' else None,
        message='Java authoritative result',
    )


def _run_to_external_wait(thread_id: str, *, graph=None):
    graph, _, hitl_wait = _run_to_user_wait(thread_id, graph=graph)
    hitl_payload = _hitl_payload(hitl_wait)
    result = resume_hitl_langgraph_agent(
        graph=graph,
        runtime_thread_id=thread_id,
        payload=hitl_payload,
        allow_business_actions=False,
        business_date=date(2026, 8, 28),
        employee_id='E10001',
    )
    snapshot = graph.get_state(_config(thread_id))
    external_wait = ExternalWaitMarker.model_validate(snapshot.values['external_wait'])
    return graph, result, hitl_payload, external_wait


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
        message='外部审批已通过。' if decision == 'APPROVED' else '外部审批已拒绝。',
    )


def test_ea1_confirmed_expense_sequentially_waits_then_approved_ends():
    graph, first, _, wait = _run_to_external_wait('ea1-approved')
    snapshot = graph.get_state(_config('ea1-approved'))
    assert '__interrupt__' in first
    assert snapshot.next == ('external_wait_node',)
    assert snapshot.values['hitl_result']['decision'] == 'CONFIRMED'

    result = resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea1-approved',
        payload=_external_payload(wait),
        allow_business_actions=False,
        business_date=date(2026, 8, 29),
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'external_approved'
    assert result['answer'] == '外部审批已通过。'
    assert result['action_proposal'] is None
    assert graph.get_state(_config('ea1-approved')).next == ()


def test_ea2_rejected_expense_ends_without_describing_submit_failure():
    graph, _, _, wait = _run_to_external_wait('ea2-rejected')
    result = resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea2-rejected',
        payload=_external_payload(wait, 'REJECTED'),
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'external_rejected'
    assert result['answer'] == '外部审批已拒绝。'
    assert graph.get_state(_config('ea2-rejected')).next == ()


def test_ea3_confirmed_annual_leave_directly_ends_without_external_wait():
    graph, _, wait = _run_to_user_wait('ea3-leave', expense=False)
    payload = _hitl_payload(wait, request_id='LR-001')
    result = resume_hitl_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea3-leave',
        payload=payload,
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'hitl_confirmed'
    assert result.get('external_wait') is None
    assert graph.get_state(_config('ea3-leave')).next == ()


def test_ea4_cancelled_expense_ends_without_external_wait():
    graph, _, wait = _run_to_user_wait('ea4-cancelled')
    result = resume_hitl_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea4-cancelled',
        payload=_hitl_payload(wait, decision='CANCELLED'),
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'hitl_cancelled'
    assert result.get('external_wait') is None
    assert graph.get_state(_config('ea4-cancelled')).next == ()


def test_ea5_confirmed_expense_without_request_id_fails_closed():
    graph, _, wait = _run_to_user_wait('ea5-missing-request')
    with pytest.raises(RuntimeError, match='request_id'):
        resume_hitl_langgraph_agent(
            graph=graph,
            runtime_thread_id='ea5-missing-request',
            payload=_hitl_payload(wait, request_id=None),
            employee_id='E10001',
        )
    snapshot = graph.get_state(_config('ea5-missing-request'))
    assert snapshot.values.get('external_wait') is None


def test_ea6_external_wait_id_is_stable_across_node_reentry():
    graph, _, _, wait = _run_to_external_wait('ea6-stable')
    assert wait == ExternalWaitMarker.for_execution(wait.execution_id, wait.request_id)
    resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea6-stable',
        payload=_external_payload(wait),
        employee_id='E10001',
    )
    persisted = ExternalWaitMarker.model_validate(
        graph.get_state(_config('ea6-stable')).values['external_wait'],
    )
    assert persisted.wait_id == wait.wait_id


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('wait_id', 'extwait_' + '0' * 64),
        ('execution_id', 'ex_' + '0' * 32),
        ('request_id', 'EXP-WRONG'),
    ],
)
def test_ea7_ea8_ea9_wrong_external_correlation_conflicts(field, value):
    graph, _, _, wait = _run_to_external_wait(f'correlation-{field}')
    payload = _external_payload(wait).model_copy(update={field: value})
    decision = inspect_external_resume(
        graph.get_state(_config(f'correlation-{field}')),
        payload,
        employee_id='E10001',
    )
    assert decision.mode is RecoveryMode.UNSAFE_REPLAY


def test_ea10_actor_mismatch_conflicts_without_mutation():
    graph, _, _, wait = _run_to_external_wait('ea10-actor')
    before = graph.get_state(_config('ea10-actor'))
    decision = inspect_external_resume(before, _external_payload(wait), employee_id='E20002')
    assert decision.mode is RecoveryMode.CONFLICT_ACTOR_SCOPE
    after = graph.get_state(_config('ea10-actor'))
    assert after.values == before.values
    assert after.next == before.next


def test_ea11_external_resume_allows_changed_business_date():
    graph, _, _, wait = _run_to_external_wait('ea11-date')
    result = resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea11-date',
        payload=_external_payload(wait),
        business_date=date(2099, 1, 1),
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'external_approved'


def test_ea12_external_terminal_continuation_ignores_current_business_permissions():
    graph, _, _, wait = _run_to_external_wait('ea12-permissions')
    result = resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea12-permissions',
        payload=_external_payload(wait),
        allow_eval=False,
        allow_business_actions=False,
        employee_id='E10001',
    )
    assert result['stop_reason'] == 'external_approved'


def test_ea13_no_planner_call_after_external_resume():
    graph, _, _, wait = _run_to_external_wait('ea13-planner')
    with patch('app.agents.planner_node.call_llm', side_effect=AssertionError('planner called')):
        result = resume_external_langgraph_agent(
            graph=graph,
            runtime_thread_id='ea13-planner',
            payload=_external_payload(wait),
            employee_id='E10001',
        )
    assert result['stop_reason'] == 'external_approved'


def test_ea14_no_tool_call_after_external_resume():
    graph, _, _, wait = _run_to_external_wait('ea14-tool')
    with patch('app.agents.tool_executor_node.expense_proposal_tool') as tool:
        tool.invoke.side_effect = AssertionError('tool called')
        result = resume_external_langgraph_agent(
            graph=graph,
            runtime_thread_id='ea14-tool',
            payload=_external_payload(wait),
            employee_id='E10001',
        )
    tool.invoke.assert_not_called()
    assert result['stop_reason'] == 'external_approved'


def test_ea15_completed_exact_external_replay_is_noop():
    graph, _, _, wait = _run_to_external_wait('ea15-completed')
    payload = _external_payload(wait)
    resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea15-completed',
        payload=payload,
        employee_id='E10001',
    )
    before = graph.get_state(_config('ea15-completed'))
    decision = inspect_external_resume(before, payload, employee_id='E10001')
    assert decision.mode is RecoveryMode.EXTERNAL_COMPLETED
    assert graph.get_state(_config('ea15-completed')).values == before.values


def test_ea16_completed_different_external_decision_conflicts():
    graph, _, _, wait = _run_to_external_wait('ea16-different')
    approved = _external_payload(wait)
    resume_external_langgraph_agent(
        graph=graph,
        runtime_thread_id='ea16-different',
        payload=approved,
        employee_id='E10001',
    )
    decision = inspect_external_resume(
        graph.get_state(_config('ea16-different')),
        _external_payload(wait, 'REJECTED'),
        employee_id='E10001',
    )
    assert decision.mode is RecoveryMode.UNSAFE_REPLAY


def test_ea17_repeated_confirmed_hitl_returns_same_external_wait():
    graph, _, hitl_payload, wait = _run_to_external_wait('ea17-response-loss')
    before = graph.get_state(_config('ea17-response-loss'))
    decision = inspect_hitl_resume(
        before,
        hitl_payload,
        employee_id='E10001',
        allow_business_actions=False,
    )
    assert decision.mode is RecoveryMode.WAITING_EXTERNAL
    assert decision.external_wait == wait.model_dump()
    after = graph.get_state(_config('ea17-response-loss'))
    assert after.values == before.values
    assert after.next == ('external_wait_node',)
    assert len(after.interrupts) == 1


def test_normal_chat_recovery_returns_waiting_external_without_question_or_date_match():
    graph, _, _, wait = _run_to_external_wait('external-chat-wait')
    decision = inspect_recovery(
        graph.get_state(_config('external-chat-wait')),
        question='这是一个完全不同的新问题',
        business_date=date(2099, 1, 1),
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=False,
    )
    assert decision.mode is RecoveryMode.WAITING_EXTERNAL
    assert decision.external_wait == wait.model_dump()


def test_external_finalize_crash_replays_with_invoke_none():
    calls = {'finalize': 0}

    def crash_once(state):
        calls['finalize'] += 1
        if calls['finalize'] == 1:
            raise RuntimeError('simulated external finalizer crash')
        return state

    with patch('app.agents.langgraph_agent.finalize_node', side_effect=crash_once):
        graph = compile_agent_loop_graph(checkpointer=InMemorySaver())
        graph, _, _, wait = _run_to_external_wait('external-finalize-crash', graph=graph)
        payload = _external_payload(wait)
        with pytest.raises(RuntimeError, match='simulated external finalizer crash'):
            resume_external_langgraph_agent(
                graph=graph,
                runtime_thread_id='external-finalize-crash',
                payload=payload,
                employee_id='E10001',
            )

    snapshot = graph.get_state(_config('external-finalize-crash'))
    assert snapshot.next == ('finalize_node',)
    assert inspect_external_resume(
        snapshot, payload, employee_id='E10001',
    ).mode is RecoveryMode.EXTERNAL_CONTINUATION
    result = resume_langgraph_agent(
        graph=graph,
        runtime_thread_id='external-finalize-crash',
        employee_id='E10001',
    )
    assert result['external_result'] == payload.model_dump()
    assert graph.get_state(_config('external-finalize-crash')).next == ()


def test_external_contract_forbids_trusted_or_secret_checkpoint_fields():
    marker = ExternalWaitMarker.for_execution('ex_' + 'a' * 32, 'EXP-001')
    assert set(marker.model_dump()) == {
        'schema_version', 'kind', 'wait_id', 'execution_id', 'action_type', 'request_id',
    }
    with pytest.raises(ValidationError):
        ExternalWaitMarker.model_validate({**marker.model_dump(), 'employee_id': 'E10001'})
    with pytest.raises(ValidationError):
        ExternalResumePayload.model_validate({
            **_external_payload(marker).model_dump(),
            'external_webhook_secret': 'forbidden',
        })


def test_external_resume_message_rejects_control_characters():
    marker = ExternalWaitMarker.for_execution('ex_' + 'b' * 32, 'EXP-002')
    payload = _external_payload(marker).model_dump()
    with pytest.raises(ValidationError):
        ExternalResumePayload.model_validate({**payload, 'message': 'approved\nforged'})
