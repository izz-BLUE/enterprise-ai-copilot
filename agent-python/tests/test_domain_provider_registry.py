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
    DomainProviderRegistry,
    ExpenseProvider,
    LeaveProvider,
    PurchaseProvider,
)
from app.agents.planner_node import MAX_PLANNER_STEPS, visible_tools
from app.agents.tool_executor_node import MAX_TOOL_CALLS, tool_executor_node
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
    PURCHASE_PROPOSAL_TOOL_NAME,
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


def _decision(tool_name: str, *, expense_reason: str | None = None) -> PlannerDecision:
    arguments = {}
    reason_code = 'need_expense_proposal'
    if tool_name == TRAVEL_RECORD_TOOL_NAME:
        reason_code = 'need_travel_history'
    elif tool_name == INVOICE_VERIFY_TOOL_NAME:
        arguments = {'invoice_id': 'INV-1'}
        reason_code = 'need_invoice_verify'
    elif tool_name == RAG_TOOL_NAME:
        arguments = {'question': QUESTION}
        reason_code = 'need_knowledge'
    return PlannerDecision.model_validate({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
        'expense_reason': expense_reason,
    })


@pytest.mark.parametrize('tool_name', [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME])
def test_expense_provider_reason_first_covers_travel_and_invoice(tool_name):
    decision, updates = ExpenseProvider().postprocess_decision(
        _decision(tool_name),
        [tool_name, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION),
    )

    assert decision.tool_name == EXPENSE_PROPOSAL_TOOL_NAME
    assert updates == {'request_expense_reason': None}


@pytest.mark.parametrize('tool_name', [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME])
def test_expense_provider_reason_first_normalizes_whitespace_reason(tool_name):
    decision, updates = ExpenseProvider().postprocess_decision(
        _decision(tool_name, expense_reason='   '),
        [tool_name, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION),
    )

    assert decision.tool_name == EXPENSE_PROPOSAL_TOOL_NAME
    assert updates == {'request_expense_reason': None}


@pytest.mark.parametrize('tool_name', [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME])
def test_expense_provider_reason_first_preserves_valid_reason(tool_name):
    decision, updates = ExpenseProvider().postprocess_decision(
        _decision(tool_name, expense_reason='客户拜访'),
        [tool_name, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION),
    )

    assert decision.tool_name == tool_name
    assert decision.expense_reason == '客户拜访'
    assert updates == {'request_expense_reason': '客户拜访'}


def test_expense_provider_continuation_reason_is_not_overridden_by_reason_first():
    decision, updates = ExpenseProvider().postprocess_decision(
        _decision(TRAVEL_RECORD_TOOL_NAME, expense_reason='客户拜访'),
        [TRAVEL_RECORD_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(
            question='客户拜访',
            continuation_original_request=QUESTION,
        ),
    )

    assert decision.tool_name == TRAVEL_RECORD_TOOL_NAME
    assert decision.expense_reason == '客户拜访'
    assert updates == {'request_expense_reason': '客户拜访'}


class _CountingExpenseProvider(ExpenseProvider):
    def __init__(self):
        self.postprocess_calls = 0

    def postprocess_decision(self, decision, tools, context):
        self.postprocess_calls += 1
        return super().postprocess_decision(decision, tools, context)


class _CountingLeaveProvider(LeaveProvider):
    def __init__(self):
        self.postprocess_calls = 0

    def postprocess_decision(self, decision, tools, context):
        self.postprocess_calls += 1
        return super().postprocess_decision(decision, tools, context)


def test_registry_runs_expense_owner_before_leave_provider_for_misrouted_proposal():
    expense = _CountingExpenseProvider()
    leave = _CountingLeaveProvider()
    registry = DomainProviderRegistry((expense, leave))

    decision, updates = registry.postprocess_decision(
        _decision(EXPENSE_PROPOSAL_TOOL_NAME),
        [RAG_TOOL_NAME, LEAVE_PROPOSAL_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question='帮我请明天年假'),
    )

    assert decision.tool_name == RAG_TOOL_NAME
    assert decision.arguments == {'question': '帮我请明天年假'}
    assert updates == {'request_expense_reason': None}
    assert expense.postprocess_calls == 1
    assert leave.postprocess_calls == 1


def test_registry_keeps_generic_expense_proposal_redirect():
    decision, _ = DOMAIN_PROVIDER_REGISTRY.postprocess_decision(
        _decision(EXPENSE_PROPOSAL_TOOL_NAME),
        [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question='公司的年假制度是什么'),
    )

    assert decision.tool_name == RAG_TOOL_NAME


def test_registry_runs_expense_provider_once_for_expense_proposal():
    expense = _CountingExpenseProvider()
    registry = DomainProviderRegistry((expense, LeaveProvider()))

    decision, updates = registry.postprocess_decision(
        _decision(EXPENSE_PROPOSAL_TOOL_NAME, expense_reason='客户拜访'),
        [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION, request_expense_reason='客户拜访'),
    )

    assert decision.tool_name == EXPENSE_PROPOSAL_TOOL_NAME
    assert updates == {'request_expense_reason': '客户拜访'}
    assert expense.postprocess_calls == 1


def test_registry_runs_expense_provider_for_expense_rag_freeze():
    expense = _CountingExpenseProvider()
    registry = DomainProviderRegistry((expense, LeaveProvider()))

    decision, updates = registry.postprocess_decision(
        _decision(RAG_TOOL_NAME, expense_reason='错误的新原因'),
        [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(
            question=QUESTION,
            request_expense_reason='客户拜访',
            step_count=1,
        ),
    )

    assert decision.tool_name == RAG_TOOL_NAME
    assert decision.expense_reason == '客户拜访'
    assert updates == {'request_expense_reason': '客户拜访'}
    assert expense.postprocess_calls == 1


def test_registry_postprocess_ambiguity_still_fails_closed():
    registry = DomainProviderRegistry((ExpenseProvider(), LeaveProvider()))

    with pytest.raises(DomainProviderAmbiguityError):
        registry.postprocess_decision(
            _decision(EXPENSE_PROPOSAL_TOOL_NAME),
            [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
            DomainContext(question='帮我请明天年假，并报销最近一次出差。'),
        )


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


def _completion_item(tool_name: str, payload: dict) -> dict:
    return {
        'tool_name': tool_name,
        'arguments': {},
        'status': 'success',
        'observation': json.dumps(payload, ensure_ascii=False),
    }


@pytest.mark.parametrize('tool_name', [
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
])
def test_structured_business_failure_is_not_completed(tool_name):
    item = _completion_item(tool_name, {
        'success': False,
        'error_code': 'FIXTURE_FAILURE',
    })

    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(item) is False


@pytest.mark.parametrize('tool_name', [
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
])
def test_structured_business_success_remains_completed(tool_name):
    item = _completion_item(tool_name, {'success': True})

    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(item) is True


def test_leave_completion_contract_distinguishes_read_and_action_goals():
    contract = DOMAIN_PROVIDER_REGISTRY.completion_contract([
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    ])

    assert '当前目标只有查询本人年假余额' in contract
    assert 'action=finish' in contract
    assert 'reason_code=task_complete' in contract
    assert '不得输出 finish/cannot_complete 或 refuse/cannot_complete' in contract
    assert '只有当用户目标还包含请假申请或准备申请时' in contract
    assert 'success=false' in contract


def test_registry_preserves_proposal_completion_semantics():
    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(_completion_item(
        EXPENSE_PROPOSAL_TOOL_NAME,
        {'success': True, 'action_proposal': None, 'missing_fields': ['invoice_ids']},
    )) is False
    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(_completion_item(
        EXPENSE_PROPOSAL_TOOL_NAME,
        {'success': True, 'action_proposal': {'action_type': 'EXPENSE_CLAIM'}},
    )) is True

    for kind in ('clarification', 'rejection', 'proposal'):
        assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(_completion_item(
            PURCHASE_PROPOSAL_TOOL_NAME,
            {'success': True, 'kind': kind, 'action_proposal': None},
        )) is True

    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(_completion_item(
        LEAVE_PROPOSAL_TOOL_NAME,
        {'success': True, 'kind': 'clarification', 'action_proposal': None},
    )) is True


def test_purchase_provider_still_owns_purchase_completion_semantics():
    assert PurchaseProvider().is_completed_success(_completion_item(
        PURCHASE_PROPOSAL_TOOL_NAME,
        {'success': True, 'kind': 'proposal', 'action_proposal': {}},
    )) is True


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
