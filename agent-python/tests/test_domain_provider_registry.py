"""P4-1 Domain Provider contract 与安全边界测试。"""

from __future__ import annotations

import copy
import json
from datetime import date
from unittest.mock import patch

import pytest

from app.agents.domain_provider_registry import (
    DOMAIN_PROVIDER_REGISTRY,
    DomainContext,
    DomainProviderAmbiguityError,
    DomainProviderRegistry,
    DomainToolCallRejected,
    ExpenseProvider,
    LeaveProvider,
)
from app.agents.planner_node import (
    MAX_PLANNER_STEPS,
    _planner_validation_metadata,
    visible_tools,
)
from app.agents.tool_executor_node import MAX_TOOL_CALLS, tool_executor_node
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
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


def _finish_decision(answer: str = '已完成。') -> PlannerDecision:
    return PlannerDecision.model_validate({
        'action': 'finish',
        'answer': answer,
        'reason_code': 'task_complete',
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


@pytest.mark.parametrize(
    ('tool_name', 'domain_key'),
    [
        (LEAVE_BALANCE_TOOL_NAME, 'leave'),
        (LEAVE_REQUEST_TOOL_NAME, 'leave'),
        (LEAVE_PROPOSAL_TOOL_NAME, 'leave'),
        (TRAVEL_RECORD_TOOL_NAME, 'expense'),
        (INVOICE_VERIFY_TOOL_NAME, 'expense'),
        (EXPENSE_PROPOSAL_TOOL_NAME, 'expense'),
        (EXPENSE_STATUS_TOOL_NAME, 'expense'),
    ],
)
def test_registry_classifies_all_domain_tools_by_owner(tool_name, domain_key):
    provider = DOMAIN_PROVIDER_REGISTRY.provider_for_tool(tool_name)

    assert provider is not None
    assert provider.domain_key == domain_key


def test_expense_legal_tools_filter_leave_domain_tools_but_keep_platform_tools():
    tools = [
        RAG_TOOL_NAME,
        EVAL_TOOL_NAME,
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    ]

    legal = DOMAIN_PROVIDER_REGISTRY.legal_tools(
        tools,
        _context(continuation_original_request=QUESTION),
    )

    assert RAG_TOOL_NAME in legal
    assert EVAL_TOOL_NAME in legal
    assert EXPENSE_STATUS_TOOL_NAME in legal
    assert not set(legal) & {
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    }


def test_leave_legal_tools_filter_expense_domain_tools_but_keep_platform_tools():
    tools = [
        RAG_TOOL_NAME,
        EVAL_TOOL_NAME,
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    ]

    legal = DOMAIN_PROVIDER_REGISTRY.legal_tools(
        tools,
        DomainContext(question='帮我请明天年假'),
    )

    assert RAG_TOOL_NAME in legal
    assert EVAL_TOOL_NAME in legal
    assert set(legal) >= {
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    }
    assert not set(legal) & {
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    }


@pytest.mark.parametrize(
    ('question', 'continuation_original_request', 'tool_name'),
    [
        ('拜访客户', QUESTION, LEAVE_PROPOSAL_TOOL_NAME),
        ('帮我请明天年假', None, EXPENSE_PROPOSAL_TOOL_NAME),
    ],
)
def test_registry_rejects_cross_domain_tool_call(question, continuation_original_request, tool_name):
    with pytest.raises(DomainToolCallRejected) as error:
        DOMAIN_PROVIDER_REGISTRY.validate_tool_call(
            tool_name,
            {},
            DomainContext(
                question=question,
                continuation_original_request=continuation_original_request,
                request_expense_reason='客户拜访' if continuation_original_request else None,
            ),
        )

    assert error.value.reason_code == 'domain_tool_mismatch'
    assert str(error.value) == '当前请求领域与目标 Tool 所属领域不一致，已拒绝执行。'


def test_expense_continuation_blocks_leave_tool_before_invoke():
    state = {
        'question': '拜访客户',
        'employee_id': 'E10001',
        'allow_business_actions': True,
        'business_date': date(2026, 8, 26),
        'request_expense_reason': '拜访客户',
        'continuation_original_request': QUESTION,
        'action_proposal': None,
        'tool_history': _history(),
        'tool_call_count': 0,
        'planner_decision': {
            'action': 'tool',
            'tool_name': LEAVE_PROPOSAL_TOOL_NAME,
            'arguments': {},
            'reason_code': 'need_proposal',
        },
    }

    with patch('app.agents.tool_executor_node.leave_proposal_tool') as proposal:
        result = tool_executor_node(
            checkpoint_safe_state(state), runtime_for_state(state)
        )

    proposal.assert_not_called()
    assert result['stop_reason'] == 'domain_tool_mismatch'
    assert result['tool_call_count'] == 0
    assert result['tool_history'][-1]['status'] == 'blocked'
    assert json.loads(result['tool_history'][-1]['observation'])['reason'] == (
        'domain_tool_mismatch'
    )


def test_unknown_provider_keeps_current_tool_set():
    tools = [RAG_TOOL_NAME, LEAVE_PROPOSAL_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]

    assert DOMAIN_PROVIDER_REGISTRY.legal_tools(
        tools,
        DomainContext(question='帮我查询今天的天气'),
    ) == tools


@pytest.mark.parametrize(
    'context',
    [
        _context(continuation_original_request=QUESTION),
        DomainContext(question='帮我请明天年假'),
    ],
)
def test_platform_tools_remain_legal_for_each_resolved_domain(context):
    assert DOMAIN_PROVIDER_REGISTRY.legal_tools(
        [RAG_TOOL_NAME, EVAL_TOOL_NAME], context
    ) == [RAG_TOOL_NAME, EVAL_TOOL_NAME]


def test_planner_visible_tools_are_restricted_to_expense_domain():
    upstream = visible_tools(
        employee_id='E10001',
        allow_eval=True,
        allow_business_actions=True,
        java_base_url='http://127.0.0.1:8080',
        java_internal_token='test-internal-token',
    )

    legal = DOMAIN_PROVIDER_REGISTRY.legal_tools(
        upstream,
        _context(continuation_original_request=QUESTION),
    )

    assert set(legal) <= {
        RAG_TOOL_NAME,
        EVAL_TOOL_NAME,
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    }
    assert not set(legal) & {
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    }


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


def test_expense_provider_completion_recovery_selects_pending_invoice_then_proposal():
    provider = ExpenseProvider()
    tools = [
        RAG_TOOL_NAME,
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
    ]

    first = provider.recover_completion_decision(
        _finish_decision(),
        tools,
        _context(tool_history=tuple(_history())),
        'expense_proposal_missing',
    )
    assert first is not None
    assert first.tool_name == INVOICE_VERIFY_TOOL_NAME
    assert first.arguments == {'invoice_id': 'INV-1'}

    second = provider.recover_completion_decision(
        _finish_decision(),
        tools,
        _context(tool_history=tuple(_history('INV-1'))),
        'expense_proposal_missing',
    )
    assert second is not None
    assert second.tool_name == INVOICE_VERIFY_TOOL_NAME
    assert second.arguments == {'invoice_id': 'INV-2'}

    final = provider.recover_completion_decision(
        _finish_decision(),
        tools,
        _context(tool_history=tuple(_history('INV-1', 'INV-2'))),
        'expense_proposal_missing',
    )
    assert final is not None
    assert final.tool_name == EXPENSE_PROPOSAL_TOOL_NAME
    assert final.arguments == {}


def test_expense_provider_completion_recovery_stays_fail_closed_without_selected_trip():
    assert ExpenseProvider().recover_completion_decision(
        _finish_decision(),
        [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(tool_history=tuple()),
        'expense_proposal_missing',
    ) is None


def test_expense_provider_completion_recovery_handles_single_invoice():
    provider = ExpenseProvider()
    history = _history()
    travel_payload = json.loads(history[0]['observation'])
    travel_payload['items'][0]['expense_documents'] = [{'invoice_id': 'INV-1'}]
    history[0]['observation'] = json.dumps(travel_payload)
    tools = [
        RAG_TOOL_NAME,
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
    ]

    invoice = provider.recover_completion_decision(
        _finish_decision(),
        tools,
        _context(tool_history=tuple(history)),
        'expense_proposal_missing',
    )
    assert invoice is not None
    assert invoice.tool_name == INVOICE_VERIFY_TOOL_NAME
    assert invoice.arguments == {'invoice_id': 'INV-1'}

    history.extend(_history('INV-1')[1:])
    proposal = provider.recover_completion_decision(
        _finish_decision(),
        tools,
        _context(tool_history=tuple(history)),
        'expense_proposal_missing',
    )
    assert proposal is not None
    assert proposal.tool_name == EXPENSE_PROPOSAL_TOOL_NAME


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


def test_leave_proposal_business_failure_cannot_finish():
    history = ({
        'tool_name': LEAVE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': False, 'error_code': 'FIXTURE_FAILURE',
        }),
    },)

    with pytest.raises(ValueError, match='leave_proposal_tool'):
        LeaveProvider().validate_completion(
            _finish_decision(), [LEAVE_PROPOSAL_TOOL_NAME],
            DomainContext(question='帮我请明天年假', tool_history=history),
        )


def test_leave_proposal_clarification_still_allows_finish():
    history = ({
        'tool_name': LEAVE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True, 'kind': 'clarification', 'action_proposal': None,
        }),
    },)

    LeaveProvider().validate_completion(
        _finish_decision('请补充请假日期。'), [LEAVE_PROPOSAL_TOOL_NAME],
        DomainContext(question='帮我请年假', tool_history=history),
    )


def test_expense_proposal_business_failure_cannot_finish():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': False, 'error_code': 'FIXTURE_FAILURE',
        }),
    },)

    with pytest.raises(ValueError, match='expense_proposal_tool'):
        ExpenseProvider().validate_completion(
            _finish_decision(), [EXPENSE_PROPOSAL_TOOL_NAME],
            DomainContext(question=QUESTION, tool_history=history),
        )


def test_expense_allowed_clarification_still_allows_finish():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True, 'action_proposal': None, 'missing_fields': ['reason'],
        }),
    },)

    ExpenseProvider().validate_completion(
        _finish_decision('请补充报销原因。'), [EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION, tool_history=history),
    )


def test_expense_reason_clarification_is_terminal_for_planner():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['reason'],
            'message': '请提供本次报销原因。',
        }),
    },)

    assert ExpenseProvider().terminal_clarification(
        DomainContext(question=QUESTION, tool_history=history)
    ) == '请提供本次报销原因。'


def test_expense_invoice_clarification_is_not_terminal_for_planner():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['invoice_ids'],
            'message': '请补充发票信息。',
        }),
    },)

    assert ExpenseProvider().terminal_clarification(
        DomainContext(question=QUESTION, tool_history=history)
    ) is None


def test_expense_terminal_clarification_uses_latest_proposal_result():
    history = (
        {
            'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
            'status': 'success',
            'observation': json.dumps({
                'success': True,
                'kind': 'clarification',
                'action_proposal': None,
                'missing_fields': ['reason'],
                'message': '请提供本次报销原因。',
            }),
        },
        {
            'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
            'status': 'success',
            'observation': json.dumps({
                'success': True,
                'kind': 'proposal',
                'action_proposal': {'action_type': 'EXPENSE_CLAIM'},
                'missing_fields': [],
            }),
        },
    )

    assert ExpenseProvider().terminal_clarification(
        DomainContext(question=QUESTION, tool_history=history)
    ) is None


def test_expense_invoice_clarification_still_cannot_finish():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True, 'action_proposal': None, 'missing_fields': ['invoice_ids'],
        }),
    },)

    with pytest.raises(ValueError, match='expense_proposal_tool'):
        ExpenseProvider().validate_completion(
            _finish_decision(), [EXPENSE_PROPOSAL_TOOL_NAME],
            DomainContext(question=QUESTION, tool_history=history),
        )


def test_expense_proposal_still_allows_finish():
    history = ({
        'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'action_proposal': {'action_type': 'EXPENSE_CLAIM'},
            'missing_fields': [],
        }),
    },)

    ExpenseProvider().validate_completion(
        _finish_decision('已生成报销草稿。'), [EXPENSE_PROPOSAL_TOOL_NAME],
        DomainContext(question=QUESTION, tool_history=history),
    )


@pytest.mark.parametrize(
    ('tools', 'history'),
    [
        ([TRAVEL_RECORD_TOOL_NAME], tuple(_history())),
        ([TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME], tuple(_history())),
        ([TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME], tuple(_history('INV-1'))),
        (
            [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
            tuple(_history('INV-1', 'INV-2')),
        ),
    ],
)
def test_expense_finish_is_rejected_without_business_successful_proposal(tools, history):
    with pytest.raises(ValueError, match='expense_proposal_tool'):
        ExpenseProvider().validate_completion(
            _finish_decision(),
            tools,
            DomainContext(
                question=QUESTION,
                request_expense_reason='客户拜访',
                tool_history=history,
            ),
        )


def test_expense_completion_contract_warns_when_prerequisite_tools_are_visible():
    contract = ExpenseProvider().completion_contract([
        RAG_TOOL_NAME, TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME,
    ])

    assert '报销申请 prerequisite 阶段' in contract
    assert '当前可见依赖 Tool 成功不代表整个报销申请已完成' in contract
    assert '不得直接 finish' in contract
    assert EXPENSE_PROPOSAL_TOOL_NAME not in contract


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

    assert DOMAIN_PROVIDER_REGISTRY.is_completed_success(_completion_item(
        LEAVE_PROPOSAL_TOOL_NAME,
        {'success': True, 'kind': 'clarification', 'action_proposal': None},
    )) is True


@pytest.mark.parametrize('question, expected', [
    ('根据最近一次已批准的出差和对应发票准备报销。', True),
    ('报销TRIP-HISTORY-001对应发票', False),
    ('公司的报销流程是什么？', False),
])
def test_expense_provider_distinguishes_action_from_read_only_intent(question, expected):
    assert ExpenseProvider().is_business_action_intent(
        DomainContext(question=question)
    ) is expected


@pytest.mark.parametrize('tool_name, question', [
    (LEAVE_BALANCE_TOOL_NAME, '请查询我的年假余额'),
    (LEAVE_REQUEST_TOOL_NAME, '请查询我的请假记录'),
    (EXPENSE_STATUS_TOOL_NAME, '请查询我的报销状态'),
    (RAG_TOOL_NAME, '公司的年假制度是什么'),
])
def test_platform_completion_guard_rejects_latest_read_business_failure(tool_name, question):
    history = (_completion_item(tool_name, {
        'success': False, 'error_code': 'FIXTURE_FAILURE',
    }),)

    with pytest.raises(ValueError, match='business success=false') as exc_info:
        DOMAIN_PROVIDER_REGISTRY.validate_completion(
            _finish_decision(), [tool_name],
            DomainContext(question=question, tool_history=history),
        )

    assert DOMAIN_PROVIDER_REGISTRY.validation_metadata(str(exc_info.value)) == (
        'planner_completion_validation',
        'structured_tool_business_failure',
        str(exc_info.value),
    )
    assert _planner_validation_metadata(exc_info.value) == (
        'planner_completion_validation',
        'structured_tool_business_failure',
        str(exc_info.value),
    )


def test_platform_completion_guard_allows_read_failure_after_retry_success():
    history = (
        _completion_item(LEAVE_BALANCE_TOOL_NAME, {
            'success': False, 'error_code': 'FIXTURE_FAILURE',
        }),
        _completion_item(LEAVE_BALANCE_TOOL_NAME, {
            'success': True, 'annual_balance': 5,
        }),
    )

    DOMAIN_PROVIDER_REGISTRY.validate_completion(
        _finish_decision(), [LEAVE_BALANCE_TOOL_NAME],
        DomainContext(question='请查询我的年假余额', tool_history=history),
    )


def test_platform_completion_guard_allows_business_negative_facts():
    DOMAIN_PROVIDER_REGISTRY.validate_completion(
        _finish_decision(), [INVOICE_VERIFY_TOOL_NAME],
        DomainContext(
            question='请校验发票 INV-1',
            tool_history=(_completion_item(INVOICE_VERIFY_TOOL_NAME, {
                'success': True, 'invoice_id': 'INV-1', 'valid': False,
            }),),
        ),
    )

def test_platform_completion_guard_allows_rag_success():
    DOMAIN_PROVIDER_REGISTRY.validate_completion(
        _finish_decision(), [RAG_TOOL_NAME],
        DomainContext(
            question='公司的年假制度是什么',
            tool_history=(_completion_item(RAG_TOOL_NAME, {
                'success': True, 'answer': '年假制度。',
            }),),
        ),
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
