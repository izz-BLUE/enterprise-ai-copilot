"""Phase C acceptance tests for semantic Planner routing."""

import json
from datetime import date
from unittest.mock import Mock, patch

from app.agents import planner_node as planner_module
from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import authorized_tools
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state


def _state(**changes):
    value = {
        'question': '公司的年假制度是什么',
        'safe': True,
        'route': '',
        'answer': '',
        'tool_result': {},
        'sources': [],
        'reason': '',
        'category': '',
        'action_proposal': None,
        'missing_fields': [],
        'request_expense_reason': None,
        'step_count': 0,
        'tool_call_count': 0,
        'tool_history': [],
        'observation': '',
        'memory_context': None,
        'execution_history': [],
        'continuation_leave_state': None,
        'planner_decision': None,
        'stop_reason': '',
        'employee_id': 'E10001',
        'allow_eval': True,
        'allow_business_actions': True,
        'business_date': date(2026, 9, 4),
        'trace_id': 'trace-phase-c',
    }
    value.update(changes)
    return value


def _invoke_planner(value):
    return planner_module.planner_node(
        checkpoint_safe_state(value),
        runtime_for_state(value),
    )


def _tool(tool_name, arguments, reason_code):
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
    }, ensure_ascii=False)


def _finish(answer):
    return json.dumps({
        'action': 'finish',
        'answer': answer,
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def test_authorized_tools_are_question_independent_and_gate_sensitive():
    kwargs = {
        'employee_id': 'E10001',
        'allow_eval': True,
        'allow_business_actions': True,
        'java_base_url': 'http://java',
        'java_internal_token': 'internal',
        'enterprise_oa_mcp_url': 'http://oa-mcp',
    }
    expected = authorized_tools(**kwargs)

    assert expected == authorized_tools(**kwargs)
    assert RAG_TOOL_NAME in expected
    assert LEAVE_BALANCE_TOOL_NAME in expected
    assert EXPENSE_STATUS_TOOL_NAME in expected
    assert LEAVE_PROPOSAL_TOOL_NAME in expected
    assert EXPENSE_PROPOSAL_TOOL_NAME in expected

    assert LEAVE_PROPOSAL_TOOL_NAME not in authorized_tools(
        **{**kwargs, 'allow_business_actions': False}
    )
    assert EXPENSE_PROPOSAL_TOOL_NAME not in authorized_tools(
        **{**kwargs, 'employee_id': ''}
    )
    assert LEAVE_BALANCE_TOOL_NAME not in authorized_tools(
        **{**kwargs, 'java_base_url': '', 'java_internal_token': ''}
    )


def test_formal_planner_candidate_set_equals_authorized_tools_without_resolve(monkeypatch):
    monkeypatch.setenv('ENTERPRISE_OA_MCP_URL', 'http://oa-mcp')
    expected = authorized_tools(
        employee_id='E10001',
        allow_eval=True,
        allow_business_actions=True,
        java_base_url='http://java',
        java_internal_token='internal',
        enterprise_oa_mcp_url='http://oa-mcp',
    )
    captured_system_prompts = []
    raw = _tool(RAG_TOOL_NAME, {'question': 'placeholder'}, 'need_knowledge')

    for question in ('公司的年假制度是什么', '帮我查询最近一次报销状态'):
        value = _state(question=question)
        with patch.object(planner_module, 'JAVA_BASE_URL', 'http://java'), \
                patch.object(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal'), \
                patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', False), \
                patch.object(
                    planner_module, 'visible_tools',
                    side_effect=AssertionError('Phase C must not call visible_tools'),
                ), \
                patch.object(
                    planner_module.DOMAIN_PROVIDER_REGISTRY, 'resolve',
                    side_effect=AssertionError('Phase C Planner must not resolve by question'),
                ), \
                patch.object(planner_module, 'call_llm', return_value=raw) as llm:
            _invoke_planner(value)

        captured_system_prompts.append(llm.call_args.args[0])
        assert llm.call_args.args[0] == planner_module.build_planner_system_prompt(expected)

    assert captured_system_prompts[0] == captured_system_prompts[1]


def test_multi_domain_loop_uses_selected_tools_without_ambiguity_refusal():
    decisions = [
        _tool(LEAVE_BALANCE_TOOL_NAME, {}, 'need_balance'),
        _tool(EXPENSE_STATUS_TOOL_NAME, {}, 'need_expense_status'),
        _finish('余额和报销状态已查询。'),
    ]
    balance_tool = Mock()
    balance_tool.invoke.return_value = json.dumps({
        'success': True,
        'data': {'remaining_days': 5},
    }, ensure_ascii=False)
    status_tool = Mock()
    status_tool.invoke.return_value = json.dumps({
        'success': True,
        'items': [{'request_id': 'REQ-1', 'status': 'SUCCEEDED'}],
    }, ensure_ascii=False)

    with patch.object(planner_module, 'JAVA_BASE_URL', 'http://java'), \
            patch.object(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal'), \
            patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', False), \
            patch.object(
                planner_module.DOMAIN_PROVIDER_REGISTRY, 'resolve',
                side_effect=AssertionError('production loop must not resolve by question'),
            ), \
            patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
            patch('app.agents.tool_executor_node.leave_balance_tool', balance_tool), \
            patch('app.agents.tool_executor_node.expense_status_tool', status_tool):
        result = run_langgraph_agent(
            '同时查询我的年假余额和最近报销状态',
            allow_business_actions=False,
            business_date=date(2026, 9, 4),
            employee_id='E10001',
            use_planner=True,
        )

    assert llm.call_count == 3
    assert balance_tool.invoke.call_count == 1
    assert status_tool.invoke.call_count == 1
    assert [item['tool_name'] for item in result['tool_history']] == [
        LEAVE_BALANCE_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    ]
    assert result['stop_reason'] == 'task_complete'


def test_wrong_leave_proposal_is_executed_as_safe_clarification_without_hitl():
    clarification = json.dumps({
        'success': True,
        'kind': 'clarification',
        'action_proposal': None,
        'missing_fields': ['reason'],
        'message': '请补充年假申请原因。',
    }, ensure_ascii=False)
    proposal_tool = Mock()
    proposal_tool.invoke.return_value = clarification

    with patch.object(planner_module, 'JAVA_BASE_URL', 'http://java'), \
            patch.object(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal'), \
            patch('app.agents.planner_node.call_llm', side_effect=[
                _tool(LEAVE_PROPOSAL_TOOL_NAME, {}, 'need_proposal'),
                _finish('请补充年假申请原因。'),
            ]), \
            patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
        result = run_langgraph_agent(
            '公司的年假制度是什么',
            allow_business_actions=True,
            business_date=date(2026, 9, 4),
            employee_id='E10001',
            use_planner=True,
        )

    proposal_tool.invoke.assert_called_once()
    assert result['action_proposal'] is None
    assert result.get('hitl_wait') is None
    assert result['stop_reason'] == 'task_complete'


def test_wrong_expense_proposal_is_clarification_without_hitl():
    clarification = json.dumps({
        'success': True,
        'kind': 'clarification',
        'action_proposal': None,
        'missing_fields': ['reason'],
        'message': '请提供本次报销原因。',
    }, ensure_ascii=False)
    proposal_tool = Mock()
    proposal_tool.invoke.return_value = clarification

    with patch('app.agents.planner_node.call_llm', return_value=(
            _tool(EXPENSE_PROPOSAL_TOOL_NAME, {}, 'need_expense_proposal'))), \
            patch('app.agents.tool_executor_node.expense_proposal_tool', proposal_tool) as tool:
        result = run_langgraph_agent(
            '公司的报销流程是什么？',
            allow_business_actions=True,
            business_date=date(2026, 9, 4),
            employee_id='E10001',
            use_planner=True,
        )

    tool.invoke.assert_called_once()
    assert result['action_proposal'] is None
    assert result.get('hitl_wait') is None
    assert result['stop_reason'] == 'task_complete'
