"""Phase D1.3 product contract tests for supported-incomplete and identity-bound requests."""

import json
from datetime import date
from unittest.mock import Mock, patch

from app.agents import planner_node as planner_module
from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import authorized_tools
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
)

BUSINESS_DATE = date(2026, 9, 4)
RAG_RESULT = json.dumps({
    'answer': '公司年假制度见员工手册。',
    'success': True,
    'sources': ['hr/annual_leave.md'],
}, ensure_ascii=False)


def _tool(tool_name: str, arguments: dict, reason_code: str) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
    }, ensure_ascii=False)


def _finish(answer: str) -> str:
    return json.dumps({
        'action': 'finish',
        'answer': answer,
        'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _refuse(answer: str) -> str:
    return json.dumps({
        'action': 'refuse',
        'answer': answer,
        'reason_code': 'not_allowed',
    }, ensure_ascii=False)


def test_incomplete_leave_request_enters_structured_clarification():
    """Supported Leave action with missing slots must use the continuation contract."""
    with patch('app.agents.planner_node.call_llm', side_effect=[
        _tool(LEAVE_PROPOSAL_TOOL_NAME, {}, 'need_proposal'),
        _finish('请补充明确的年假日期和申请原因。'),
    ]):
        result = run_langgraph_agent(
            '我想申请一个假，但没说哪天，也没说明原因。',
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
            employee_id='E10001',
            use_planner=True,
        )

    assert result['route'] == 'action'
    assert result['stop_reason'] == 'task_complete'
    assert result['action_proposal'] is None
    assert result['missing_fields'] == ['start_date', 'end_date', 'reason']
    assert result['continuation_leave_state']['continuation_type'] == 'leave_clarification'
    assert result['continuation_leave_state']['waiting_for'] == 'date'
    assert [item['tool_name'] for item in result['tool_history']] == [
        LEAVE_PROPOSAL_TOOL_NAME,
    ]


def test_incomplete_expense_request_enters_supported_workflow_clarification():
    """An explicit expense claim is supported even when its first slot is absent."""
    with patch('app.agents.planner_node.call_llm', side_effect=[
        _tool(EXPENSE_PROPOSAL_TOOL_NAME, {}, 'need_expense_proposal'),
    ]), patch('app.agents.tool_executor_node.expense_proposal_tool') as proposal_tool:
        proposal_tool.invoke.return_value = json.dumps({
            'success': True,
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['reason'],
            'message': '请提供本次报销原因。',
        }, ensure_ascii=False)
        result = run_langgraph_agent(
            '帮我报销一下。',
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
            employee_id='E10001',
            use_planner=True,
        )

    proposal_tool.invoke.assert_called_once()
    assert result['route'] == 'action'
    assert result['stop_reason'] == 'task_complete'
    assert result['action_proposal'] is None
    assert result['missing_fields'] == ['reason']


def test_anonymous_personal_leave_balance_is_refused_without_rag_fallback():
    """Identity-bound realtime data stays unavailable when leave tools are hidden."""
    tools = authorized_tools(
        employee_id='',
        allow_eval=False,
        allow_business_actions=False,
        java_base_url='',
        java_internal_token='',
        enterprise_oa_mcp_url='',
    )
    assert tools == [RAG_TOOL_NAME]
    prompt = planner_module.build_planner_system_prompt(tools)
    assert LEAVE_BALANCE_TOOL_NAME not in prompt
    assert '个人实时、身份绑定' in prompt
    assert '不得用 RAG 或普通回答替代' in prompt

    rag_tool = Mock()
    with patch('app.agents.planner_node.call_llm', return_value=_refuse(
            '当前无法查询个人实时年假余额。')) as llm, \
            patch('app.agents.tool_executor_node.rag_answer_tool', rag_tool):
        result = run_langgraph_agent(
            '匿名用户能查询自己的年假余额吗？',
            allow_business_actions=False,
            employee_id='',
            use_planner=True,
        )

    llm.assert_called_once()
    rag_tool.invoke.assert_not_called()
    assert result['route'] == 'refuse'
    assert result['stop_reason'] == 'refused'


def test_anonymous_static_leave_policy_remains_rag_eligible():
    """Hiding personal tools must not hide static enterprise knowledge."""
    rag_tool = Mock()
    rag_tool.invoke.return_value = RAG_RESULT
    with patch('app.agents.planner_node.call_llm', side_effect=[
        _tool(RAG_TOOL_NAME, {'question': '公司的年假制度是什么'}, 'need_knowledge'),
        _finish('已查询公司年假制度。'),
    ]), patch('app.agents.tool_executor_node.rag_answer_tool', rag_tool):
        result = run_langgraph_agent(
            '公司的年假制度是什么',
            allow_business_actions=False,
            employee_id='',
            use_planner=True,
        )

    rag_tool.invoke.assert_called_once()
    assert result['route'] == 'rag'
    assert result['stop_reason'] == 'task_complete'
