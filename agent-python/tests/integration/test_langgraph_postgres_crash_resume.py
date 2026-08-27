"""Real PostgreSQL crash-resume acceptance for P3-3."""

import hashlib
import json
import os
from datetime import date
from typing import TypedDict
from unittest.mock import patch

import pytest
from langgraph.graph import END, START, StateGraph

from app.agents.langgraph_agent import (
    AgentState,
    finalize_node,
    resume_langgraph_agent,
    run_langgraph_agent,
    safety_node,
)
from app.agents.planner_node import planner_node
from app.agents.runtime_context import AgentRuntimeContext
from app.agents.tool_executor_node import tool_executor_node
from app.runtime.checkpoint_runtime import CheckpointRuntime
from app.runtime.execution_recovery import RecoveryMode
from app.schemas.execution_recovery_schema import ExecutionRecoveryMarker

_DSN = os.getenv('LANGGRAPH_CHECKPOINT_DSN', '')
pytestmark = pytest.mark.skipif(
    os.getenv('LANGGRAPH_CHECKPOINT_MODE') != 'POSTGRES' or not _DSN,
    reason='PostgreSQL crash-resume integration requires explicit POSTGRES mode and DSN',
)


def _thread_id(label: str) -> str:
    return 'rt_' + hashlib.sha256(label.encode()).hexdigest() + ':planner-v1'


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _tool(tool_name, arguments, reason_code):
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
    }, ensure_ascii=False)


def _finish(answer='done'):
    return json.dumps({
        'action': 'finish',
        'answer': answer,
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _travel_result():
    return json.dumps({
        'success': True,
        'items': [{
            'trip_id': 'TRIP-P3-3-001',
            'employee_id': 'E10001',
            'destination': '上海',
            'start_date': '2026-08-18',
            'end_date': '2026-08-20',
            'status': 'APPROVED',
            'purpose': '客户拜访',
            'expense_documents': [{'invoice_id': 'INV-P3-3-001'}],
        }],
    }, ensure_ascii=False)


def _invoice_result():
    return json.dumps({
        'success': True,
        'invoice_id': 'INV-P3-3-001',
        'valid': True,
        'duplicate': False,
        'amount': 1600,
        'category': 'HOTEL',
        'issued_at': '2026-08-19',
        'vendor': '上海如家',
        'employee_id': 'E10001',
    }, ensure_ascii=False)


def _eval_result():
    return json.dumps({
        'success': True,
        'retrieval': {'final_pass_rate': 0.99},
        'privileged_eval_material': 'evaluation-only result',
    }, ensure_ascii=False)


def _leave_proposal_result():
    return json.dumps({
        'success': True,
        'kind': 'proposal',
        'action_proposal': {
            'action_type': 'ANNUAL_LEAVE_REQUEST',
            'start_date': '2026-09-01',
            'end_date': '2026-09-01',
            'reason': 'P3-3 security audit',
            'half_day': None,
        },
        'missing_fields': [],
    }, ensure_ascii=False)


def _crashable_planner_graph(runtime: CheckpointRuntime, calls: dict):
    """Use a test-only wrapper to model a process-level unhandled graph failure."""
    graph = StateGraph(AgentState, context_schema=AgentRuntimeContext)

    def planner_with_fault(state, runtime_context):
        calls['planner'] += 1
        if calls['planner'] == 2:
            raise RuntimeError('simulated process failure after travel checkpoint')
        return planner_node(state, runtime_context)

    graph.add_node('safety_node', safety_node)
    graph.add_node('planner_node', planner_with_fault)
    graph.add_node('tool_executor_node', tool_executor_node)
    graph.add_node('finalize_node', finalize_node)
    graph.add_edge(START, 'safety_node')
    graph.add_conditional_edges(
        'safety_node',
        lambda state: 'planner_node' if state.get('safe', True) else 'finalize_node',
        {'planner_node': 'planner_node', 'finalize_node': 'finalize_node'},
    )
    graph.add_conditional_edges(
        'planner_node',
        lambda state: (
            'tool_executor_node' if state.get('stop_reason') == 'continue'
            else 'finalize_node'
        ),
        {'tool_executor_node': 'tool_executor_node', 'finalize_node': 'finalize_node'},
    )
    graph.add_edge('tool_executor_node', 'planner_node')
    graph.add_edge('finalize_node', END)
    return graph.compile(checkpointer=runtime._saver)


def _runtime():
    runtime = CheckpointRuntime(
        mode='POSTGRES', dsn=_DSN, connect_timeout_seconds=3, max_connections=3,
    )
    runtime.start()
    return runtime


@pytest.fixture(autouse=True)
def _enable_expense_tools(monkeypatch):
    monkeypatch.setenv('ENTERPRISE_OA_MCP_URL', 'http://127.0.0.1:8100/mcp')
    monkeypatch.setattr('app.agents.planner_node.JAVA_BASE_URL', 'http://127.0.0.1:8080')
    monkeypatch.setattr('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'test-internal-token')


def test_r1_to_r16_real_postgres_crash_restart_resume_and_fail_closed():
    question = '帮我报销上海出差费用。'
    business_date = date(2026, 8, 27)
    thread_id = _thread_id('p3-3-r1-r16')
    calls = {'planner': 0}

    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, calls)
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'travel_record_tool', {}, 'need_travel_history',
        )), patch('app.agents.tool_executor_node.travel_record_tool') as travel:
            travel.invoke.return_value = _travel_result()
            with pytest.raises(RuntimeError, match='simulated process failure'):
                run_langgraph_agent(
                    question,
                    use_planner=True,
                    allow_business_actions=True,
                    business_date=business_date,
                    employee_id='E10001',
                    trace_id='trace-A',
                    graph=crash_graph,
                    runtime_thread_id=thread_id,
                )

        crashed = crash_graph.get_state(_config(thread_id))
        assert crashed.next == ('planner_node',)
        assert [item['tool_name'] for item in crashed.values['tool_history']] == [
            'travel_record_tool',
        ]
        assert crashed.values['tool_call_count'] == 1
        assert crashed.values['step_count'] == 1
        marker_a = ExecutionRecoveryMarker.model_validate(
            crashed.values['execution_recovery'],
        )
        assert marker_a.execution_date_anchor == '2026-08-27'
        assert not {
            'employee_id', 'allow_eval', 'allow_business_actions',
            'business_date', 'trace_id', 'deadline_monotonic',
        }.intersection(crashed.values)
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        recovery = runtime_b.inspect_recovery(
            graph=graph,
            thread_id=thread_id,
            question=question,
            business_date=business_date,
            employee_id='E10001',
            allow_eval=False,
            allow_business_actions=False,
        )
        assert recovery.mode is RecoveryMode.RESUME
        assert recovery.pending_node == 'planner_node'
        assert recovery.execution_id == marker_a.execution_id

        invoice_calls = []
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool('invoice_verify_tool', {'invoice_id': 'INV-P3-3-001'}, 'need_invoice_verify'),
            _finish('报销材料已核验'),
        ]) as planner, \
                patch('app.agents.tool_executor_node.travel_record_tool') as travel, \
                patch('app.agents.tool_executor_node.invoice_verify_tool') as invoice:
            invoice.invoke.side_effect = lambda args: (
                invoice_calls.append(args) or _invoice_result()
            )
            result = resume_langgraph_agent(
                graph=graph,
                runtime_thread_id=thread_id,
                allow_eval=False,
                allow_business_actions=False,
                business_date=business_date,
                employee_id='E10001',
                trace_id='trace-B',
            )

        assert travel.invoke.call_count == 0
        assert invoice.invoke.call_count == 1
        assert invoice_calls[0]['trace_id'] == 'trace-B'
        assert result['tool_call_count'] == 2
        assert result['step_count'] == 3
        assert [item['tool_name'] for item in result['tool_history']] == [
            'travel_record_tool', 'invoice_verify_tool',
        ]
        assert [item['tool_name'] for item in result['execution_history']] == [
            'travel_record_tool', 'invoice_verify_tool',
        ]
        assert result['execution_recovery']['execution_id'] == marker_a.execution_id
        assert graph.get_state(_config(thread_id)).next == ()
        assert graph.get_state(_config(thread_id)).values == result

        planner_prompt = planner.call_args_list[0].args[0]
        assert 'eval_report_tool' not in planner_prompt
        assert 'leave_proposal_tool' not in planner_prompt

        # Same unfinished checkpoint is still protected against date/question drift.
        # The completed state above is intentionally not reused for these checks.
    finally:
        runtime_b.shutdown()


def test_r10_r11_real_postgres_incomplete_checkpoint_conflicts_without_resume():
    question = '原始未完成任务'
    thread_id = _thread_id('p3-3-r10-r11')
    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, {'planner': 0})
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'travel_record_tool', {}, 'need_travel_history',
        )), patch('app.agents.tool_executor_node.travel_record_tool') as travel:
            travel.invoke.return_value = _travel_result()
            with pytest.raises(RuntimeError):
                run_langgraph_agent(
                    question, use_planner=True, business_date=date(2026, 8, 27),
                    employee_id='E10001', trace_id='trace-A',
                    graph=crash_graph, runtime_thread_id=thread_id,
                )
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        assert runtime_b.inspect_recovery(
            graph=graph, thread_id=thread_id, question=question,
            business_date=date(2026, 8, 28),
            employee_id='E10001', allow_eval=False,
            allow_business_actions=False,
        ).mode is RecoveryMode.CONFLICT_DATE
        assert runtime_b.inspect_recovery(
            graph=graph, thread_id=thread_id, question='另一个任务',
            business_date=date(2026, 8, 27),
            employee_id='E10001', allow_eval=False,
            allow_business_actions=False,
        ).mode is RecoveryMode.CONFLICT_REQUEST
        assert graph.get_state(_config(thread_id)).next == ('planner_node',)
    finally:
        runtime_b.shutdown()


def test_s2_real_postgres_changed_employee_scope_conflicts_without_overwrite():
    question = '查询我的上海出差记录'
    thread_id = _thread_id('p3-3-security-actor-scope')
    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, {'planner': 0})
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'travel_record_tool', {}, 'need_travel_history',
        )), patch('app.agents.tool_executor_node.travel_record_tool') as travel:
            travel.invoke.return_value = _travel_result()
            with pytest.raises(RuntimeError, match='simulated process failure'):
                run_langgraph_agent(
                    question, use_planner=True, business_date=date(2026, 8, 27),
                    employee_id='E10001', trace_id='scope-A',
                    graph=crash_graph, runtime_thread_id=thread_id,
                )
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        before = graph.get_state(_config(thread_id))
        recovery = runtime_b.inspect_recovery(
            graph=graph, thread_id=thread_id, question=question,
            business_date=date(2026, 8, 27), employee_id='E20002',
            allow_eval=False, allow_business_actions=False,
        )
        assert recovery.mode is RecoveryMode.CONFLICT_ACTOR_SCOPE
        assert recovery.reason == 'actor_scope_changed'
        after = graph.get_state(_config(thread_id))
        assert after.next == before.next == ('planner_node',)
        assert after.values == before.values
    finally:
        runtime_b.shutdown()


def test_s3_real_postgres_revoked_eval_capability_blocks_eval_residue():
    question = '查看 RAG 评估报告'
    thread_id = _thread_id('p3-3-security-eval-residue')
    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, {'planner': 0})
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'eval_report_tool', {'report_type': 'all'}, 'need_eval',
        )), patch('app.agents.tool_executor_node.eval_report_tool') as evaluation:
            evaluation.invoke.return_value = _eval_result()
            with pytest.raises(RuntimeError, match='simulated process failure'):
                run_langgraph_agent(
                    question, use_planner=True, allow_eval=True,
                    business_date=date(2026, 8, 27), trace_id='eval-A',
                    graph=crash_graph, runtime_thread_id=thread_id,
                )
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        snapshot = graph.get_state(_config(thread_id))
        assert snapshot.next == ('planner_node',)
        assert snapshot.values['tool_history'][0]['tool_name'] == 'eval_report_tool'
        assert snapshot.values['tool_history'][0]['status'] == 'success'
        recovery = runtime_b.inspect_recovery(
            graph=graph, thread_id=thread_id, question=question,
            business_date=date(2026, 8, 27), employee_id='',
            allow_eval=False, allow_business_actions=False,
        )
        assert recovery.mode is RecoveryMode.CONFLICT_CAPABILITY
        assert recovery.reason == 'eval_capability_revoked'
        assert graph.get_state(_config(thread_id)).next == ('planner_node',)
    finally:
        runtime_b.shutdown()


def test_s4_real_postgres_revoked_business_capability_blocks_proposal_residue():
    question = '申请明天年假'
    thread_id = _thread_id('p3-3-security-business-residue')
    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, {'planner': 0})
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'leave_proposal_tool', {}, 'need_proposal',
        )), patch('app.agents.tool_executor_node.leave_proposal_tool') as proposal:
            proposal.invoke.return_value = _leave_proposal_result()
            with pytest.raises(RuntimeError, match='simulated process failure'):
                run_langgraph_agent(
                    question, use_planner=True, allow_business_actions=True,
                    business_date=date(2026, 8, 27), employee_id='E10001',
                    trace_id='proposal-A', graph=crash_graph,
                    runtime_thread_id=thread_id,
                )
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph = runtime_b.get_graph(use_planner=True)
        snapshot = graph.get_state(_config(thread_id))
        assert snapshot.next == ('planner_node',)
        assert snapshot.values['tool_history'][0]['tool_name'] == 'leave_proposal_tool'
        assert snapshot.values['tool_history'][0]['status'] == 'success'
        assert snapshot.values['action_proposal'] is not None
        recovery = runtime_b.inspect_recovery(
            graph=graph, thread_id=thread_id, question=question,
            business_date=date(2026, 8, 27), employee_id='E10001',
            allow_eval=False, allow_business_actions=False,
        )
        assert recovery.mode is RecoveryMode.CONFLICT_CAPABILITY
        assert recovery.reason == 'business_capability_revoked'
        assert graph.get_state(_config(thread_id)).next == ('planner_node',)
    finally:
        runtime_b.shutdown()


def test_r17_real_postgres_resume_failure_keeps_pending_head_for_next_retry():
    question = '重复恢复测试'
    thread_id = _thread_id('p3-3-r17')
    runtime_a = _runtime()
    try:
        crash_graph = _crashable_planner_graph(runtime_a, {'planner': 0})
        with patch('app.agents.planner_node.call_llm', return_value=_tool(
            'travel_record_tool', {}, 'need_travel_history',
        )), patch('app.agents.tool_executor_node.travel_record_tool') as travel:
            travel.invoke.return_value = _travel_result()
            with pytest.raises(RuntimeError):
                run_langgraph_agent(
                    question, use_planner=True, business_date=date(2026, 8, 27),
                    employee_id='E10001', trace_id='trace-A',
                    graph=crash_graph, runtime_thread_id=thread_id,
                )
    finally:
        runtime_a.shutdown()

    runtime_b = _runtime()
    try:
        graph_b = _crashable_planner_graph(runtime_b, {'planner': 1})
        with pytest.raises(RuntimeError, match='simulated process failure'):
            resume_langgraph_agent(
                graph=graph_b, runtime_thread_id=thread_id,
                business_date=date(2026, 8, 27), employee_id='E10001',
                trace_id='trace-B',
            )
        assert graph_b.get_state(_config(thread_id)).next == ('planner_node',)
    finally:
        runtime_b.shutdown()

    runtime_c = _runtime()
    try:
        with patch('app.agents.planner_node.call_llm', return_value=_finish('最终完成')):
            result = resume_langgraph_agent(
                graph=runtime_c.get_graph(use_planner=True),
                runtime_thread_id=thread_id,
                business_date=date(2026, 8, 27), employee_id='E10001',
                trace_id='trace-C',
            )
        assert result['tool_call_count'] == 1
        assert result['tool_history'][0]['tool_name'] == 'travel_record_tool'
        assert runtime_c.get_graph(use_planner=True).get_state(_config(thread_id)).next == ()
    finally:
        runtime_c.shutdown()


class _LegacyState(TypedDict):
    value: str


def test_r13_real_postgres_legacy_incomplete_checkpoint_is_incompatible():
    thread_id = _thread_id('p3-3-r13')
    runtime = _runtime()
    try:
        graph_builder = StateGraph(_LegacyState)

        def crash(_state):
            raise RuntimeError('legacy crash')

        graph_builder.add_node('start', lambda _state: {})
        graph_builder.add_node('crash', crash)
        graph_builder.add_edge(START, 'start')
        graph_builder.add_edge('start', 'crash')
        graph = graph_builder.compile(checkpointer=runtime._saver)
        with pytest.raises(RuntimeError, match='legacy crash'):
            graph.invoke({'value': 'legacy'}, config=_config(thread_id), durability='sync')
        snapshot = graph.get_state(_config(thread_id))
        assert snapshot.next == ('crash',)
        assert runtime.inspect_recovery(
            graph=graph, thread_id=thread_id, question='legacy', business_date=None,
            employee_id='', allow_eval=False, allow_business_actions=False,
        ).mode is RecoveryMode.INCOMPATIBLE_CHECKPOINT
    finally:
        runtime.shutdown()
