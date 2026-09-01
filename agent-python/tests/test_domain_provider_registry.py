"""P4-1 Domain Provider contract 与安全边界测试。"""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from app.agents.domain_provider_registry import (
    DOMAIN_PROVIDER_REGISTRY,
    DomainContext,
    DomainProviderAmbiguityError,
    ExpenseProvider,
    LeaveProvider,
)
from app.agents.planner_node import MAX_PLANNER_STEPS, visible_tools
from app.agents.tool_executor_node import MAX_TOOL_CALLS, tool_executor_node
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    PlannerDecision,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

QUESTION = '根据最近一次已批准的出差和对应发票准备报销。'


def _history(*verified_invoice_ids: str) -> list[dict]:
    entries = [{
        'tool_name': TRAVEL_RECORD_TOOL_NAME,
        'arguments': {},
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'items': [{
                'trip_id': 'TRIP-1',
                'status': 'APPROVED',
                'expense_documents': [
                    {'invoice_id': invoice_id} for invoice_id in ('INV-1', 'INV-2')
                ],
            }],
        }),
    }]
    entries.extend({
        'tool_name': INVOICE_VERIFY_TOOL_NAME,
        'arguments': {'invoice_id': invoice_id},
        'status': 'success',
        'observation': json.dumps({
            'success': True, 'invoice_id': invoice_id, 'valid': True,
        }),
    } for invoice_id in verified_invoice_ids)
    return entries


def _context(**changes) -> DomainContext:
    values = {
        'question': QUESTION,
        'tool_history': tuple(_history('INV-1')),
        'request_expense_reason': '客户拜访',
    }
    values.update(changes)
    return DomainContext(**values)


def test_provider_cannot_reexpose_capability_hidden_tool():
    hidden = [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]
    visible = [RAG_TOOL_NAME]
    assert DOMAIN_PROVIDER_REGISTRY.legal_tools(visible, _context()) == visible
    assert EXPENSE_PROPOSAL_TOOL_NAME not in DOMAIN_PROVIDER_REGISTRY.legal_tools(visible, _context())
    assert set(hidden) != set(visible)


def test_expense_provider_preserves_selected_trip_prerequisite_order():
    provider = ExpenseProvider()
    assert provider.legal_tools(
        [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(),
    ) == [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME]
    assert provider.legal_tools(
        [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(tool_history=tuple(_history('INV-1', 'INV-2'))),
    ) == [TRAVEL_RECORD_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]


def test_executor_second_gate_blocks_illegal_expense_proposal():
    state = {
        'question': QUESTION,
        'employee_id': 'E10001',
        'allow_business_actions': True,
        'business_date': date(2026, 8, 26),
        'request_expense_reason': '客户拜访',
        'action_proposal': None,
        'tool_history': _history('INV-1'),
        'tool_call_count': 0,
        'planner_decision': {
            'action': 'tool',
            'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
            'arguments': {},
            'reason_code': 'need_expense_proposal',
        },
    }
    result = tool_executor_node(
        checkpoint_safe_state(state), runtime_for_state(state)
    )
    assert result['stop_reason'] == 'expense_proposal_prerequisite_missing'
    assert result['tool_call_count'] == 0


def test_clarification_is_not_completion():
    provider = ExpenseProvider()
    decision = PlannerDecision.model_validate({
        'action': 'finish',
        'answer': '请补充发票信息。',
        'reason_code': 'task_complete',
    })
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True, 'action_proposal': None, 'missing_fields': ['invoice_ids'],
        }),
    },)
    with pytest.raises(ValueError, match='expense_proposal_tool'):
        provider.validate_completion(
            decision, [EXPENSE_PROPOSAL_TOOL_NAME],
            DomainContext(question=QUESTION, tool_history=history),
        )


def test_leave_provider_is_simple_and_has_no_expense_semantic_slot():
    provider = LeaveProvider()
    tools = [RAG_TOOL_NAME, LEAVE_PROPOSAL_TOOL_NAME]
    assert provider.legal_tools(tools, DomainContext(question='帮我请明天年假')) == tools
    assert provider.semantic_slots == frozenset()


def test_multiple_provider_match_fails_closed():
    with pytest.raises(DomainProviderAmbiguityError):
        DOMAIN_PROVIDER_REGISTRY.resolve(
            DomainContext(question='帮我请明天年假，并报销最近一次出差。')
        )


def test_provider_does_not_mutate_request_state():
    history = _history('INV-1')
    before = copy.deepcopy(history)
    context = _context(tool_history=tuple(history))
    DOMAIN_PROVIDER_REGISTRY.legal_tools(
        [RAG_TOOL_NAME, TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME,
         EXPENSE_PROPOSAL_TOOL_NAME],
        context,
    )
    assert history == before
    assert context.tool_history == tuple(history)


def test_capability_and_budget_values_remain_frozen():
    assert MAX_PLANNER_STEPS == 6
    assert MAX_TOOL_CALLS == 5
    assert visible_tools(
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=True,
        java_base_url='',
        java_internal_token='',
    ) == [RAG_TOOL_NAME, LEAVE_PROPOSAL_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]
