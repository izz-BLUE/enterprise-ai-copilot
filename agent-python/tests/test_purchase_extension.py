"""P4-3 Purchase Extension Proof：最小真实领域链路测试。"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from app.agents.domain_provider_registry import (
    DomainContext,
    DomainToolCallRejected,
    PurchaseProvider,
)
from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import visible_tools
from app.agents.tool_executor_node import tool_executor_node
from app.schemas.planner_schema import (
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
    PURCHASE_PROPOSAL_TOOL_NAME,
)
from app.tools.enterprise_tools import (
    purchase_budget_tool,
    purchase_policy_tool,
    purchase_proposal_tool,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

PURCHASE_QUESTION = '请帮我申请购买一台 MacBook Pro，预算 6800 元，用于开发工作。'


def _decision(tool_name: str, reason_code: str, **semantic) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': {},
        'reason_code': reason_code,
        **semantic,
    }, ensure_ascii=False)


def _finish(answer: str) -> str:
    return json.dumps({
        'action': 'finish', 'answer': answer, 'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _purchase_state(**changes) -> dict:
    state = {
        'question': PURCHASE_QUESTION,
        'allow_business_actions': True,
        'business_date': date(2026, 9, 2),
        'employee_id': 'E10001',
        'action_proposal': None,
        'tool_history': [],
        'tool_call_count': 0,
        'step_count': 0,
        'request_expense_reason': None,
        'observation': '',
        'planner_decision': None,
    }
    state.update(changes)
    return state


def test_purchase_capability_is_visible_only_for_authorized_purchase_request():
    common = {
        'employee_id': 'E10001',
        'allow_eval': False,
        'java_base_url': '',
        'java_internal_token': '',
    }
    assert PURCHASE_BUDGET_TOOL_NAME not in visible_tools(
        allow_business_actions=False, question=PURCHASE_QUESTION, **common)
    visible = visible_tools(
        allow_business_actions=True, question=PURCHASE_QUESTION, **common)
    assert visible[-3:] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]
    assert visible_tools(
        allow_business_actions=True, question='采购政策怎么规定？', **common
    ) == ['rag_answer_tool', 'leave_proposal_tool', 'expense_proposal_tool']

    direct_purchase = visible_tools(
        allow_business_actions=True, question='请帮我采购一台开发用 MacBook Pro', **common
    )
    assert direct_purchase[-3:] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]


def test_purchase_provider_requires_budget_then_policy_then_proposal_facts():
    provider = PurchaseProvider()
    context = DomainContext(
        question=PURCHASE_QUESTION,
        purchase_item='MacBook Pro',
        purchase_budget='6800.00',
        purchase_justification='开发工作',
        step_count=1,
    )
    assert provider.legal_tools([
        PURCHASE_BUDGET_TOOL_NAME, PURCHASE_POLICY_TOOL_NAME, PURCHASE_PROPOSAL_TOOL_NAME,
    ], context) == [PURCHASE_BUDGET_TOOL_NAME]
    budget_history = ({
        'tool_name': PURCHASE_BUDGET_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({'success': True, 'available_budget': '20000.00'}),
    },)
    context = DomainContext(**{**context.__dict__, 'tool_history': budget_history})
    assert provider.legal_tools([
        PURCHASE_BUDGET_TOOL_NAME, PURCHASE_POLICY_TOOL_NAME, PURCHASE_PROPOSAL_TOOL_NAME,
    ], context) == [PURCHASE_POLICY_TOOL_NAME]
    with pytest.raises(DomainToolCallRejected, match='事实尚未完成'):
        provider.validate_tool_call(PURCHASE_PROPOSAL_TOOL_NAME, {}, context)


def test_purchase_tools_are_deterministic_and_proposal_is_read_only():
    budget = json.loads(purchase_budget_tool.invoke({'employee_id': 'E10001'}))
    assert budget['success'] is True
    assert budget['available_budget'] == '20000.00'

    policy = json.loads(purchase_policy_tool.invoke({
        'item_name': 'MacBook Pro',
        'requested_budget': '6800',
        'justification': '开发工作',
    }))
    assert policy['success'] is True
    assert policy['policy_result'] == 'PASS'

    proposal = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro',
        'requested_budget': '6800',
        'justification': '开发工作',
        'context': {
            'purchase_budget': budget,
            'purchase_policy': policy,
        },
    }))
    assert proposal['kind'] == 'proposal'
    assert proposal['action_proposal']['action_type'] == 'PURCHASE_REQUEST'


def test_purchase_missing_fields_and_rejections_never_create_proposal():
    facts = {
        'purchase_budget': {'success': True, 'available_budget': '20000.00'},
        'purchase_policy': {'success': True, 'policy_result': 'PASS'},
    }
    missing_item = json.loads(purchase_proposal_tool.invoke({
        'item_name': '', 'requested_budget': '6800', 'justification': '开发工作',
        'context': facts,
    }))
    assert missing_item['kind'] == 'clarification'
    assert missing_item['action_proposal'] is None

    missing_justification = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro', 'requested_budget': '6800', 'justification': '',
        'context': facts,
    }))
    assert missing_justification['kind'] == 'clarification'
    assert missing_justification['action_proposal'] is None

    over_budget = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro', 'requested_budget': '25000', 'justification': '开发工作',
        'context': facts,
    }))
    assert over_budget['kind'] == 'rejection'
    assert over_budget['action_proposal'] is None

    denied_policy = json.loads(purchase_proposal_tool.invoke({
        'item_name': '办公椅', 'requested_budget': '6800', 'justification': '个人娱乐',
        'context': {
            'purchase_budget': facts['purchase_budget'],
            'purchase_policy': {'success': True, 'policy_result': 'FAIL', 'policy_reason': 'denied'},
        },
    }))
    assert denied_policy['kind'] == 'rejection'
    assert denied_policy['action_proposal'] is None


def test_executor_second_gate_rejects_purchase_proposal_without_fresh_facts():
    state = _purchase_state(
        planner_decision={
            'action': 'tool', 'tool_name': PURCHASE_PROPOSAL_TOOL_NAME,
            'arguments': {}, 'reason_code': 'need_purchase_proposal',
            'purchase_item': 'MacBook Pro', 'purchase_budget': '6800',
            'purchase_justification': '开发工作',
        },
    )
    result = tool_executor_node(checkpoint_safe_state(state), runtime_for_state(state))
    assert result['stop_reason'] == 'purchase_prerequisite_missing'
    assert result['tool_call_count'] == 0


def test_purchase_planner_executor_proposal_loop_reaches_confirmable_result():
    decisions = [
        _decision(
            PURCHASE_BUDGET_TOOL_NAME, 'need_purchase_budget',
            purchase_item='MacBook Pro', purchase_budget='6800',
            purchase_justification='开发工作',
        ),
        _decision(PURCHASE_POLICY_TOOL_NAME, 'need_purchase_policy'),
        _decision(PURCHASE_PROPOSAL_TOOL_NAME, 'need_purchase_proposal'),
        _finish('已生成采购申请草稿，请确认后提交。'),
    ]
    budget_tool = Mock()
    budget_tool.invoke.return_value = json.dumps({
        'success': True,
        'available_budget': '20000.00', 'source': 'fixture:purchase_budget',
    })
    policy_tool = Mock()
    policy_tool.invoke.return_value = json.dumps({
        'success': True,
        'item_name': 'MacBook Pro', 'requested_budget': '6800',
        'policy_result': 'PASS', 'policy_reason': 'ok',
    })
    with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
            patch('app.agents.tool_executor_node.purchase_budget_tool', budget_tool), \
            patch('app.agents.tool_executor_node.purchase_policy_tool', policy_tool):
        result = run_langgraph_agent(
            PURCHASE_QUESTION,
            allow_business_actions=True,
            employee_id='E10001',
            business_date=date(2026, 9, 2),
            use_planner=True,
        )

    assert result['stop_reason'] == 'task_complete'
    assert result['step_count'] == 4
    assert result['tool_call_count'] == 3
    assert result['action_proposal']['action_type'] == 'PURCHASE_REQUEST'
    assert str(result['action_proposal']['requested_budget']) == '6800'
    assert [item['tool_name'] for item in result['tool_history']] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]
