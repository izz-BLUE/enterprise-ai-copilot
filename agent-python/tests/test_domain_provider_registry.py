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
)
from app.agents.planner_node import (
    MAX_PLANNER_STEPS,
    _planner_validation_metadata,
    authorized_tools,
)
from app.agents.tool_executor_node import MAX_TOOL_CALLS, tool_executor_node
from app.agents.workflow_guard.expense_guard import ExpenseGuard
from app.agents.workflow_guard.leave_guard import LeaveGuard
from app.schemas.planner_schema import (
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


def test_expense_guard_preserves_selected_trip_prerequisite_order():
    guard = ExpenseGuard()
    assert guard.legal_tools(
        [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(),
    ) == [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME]
    assert guard.legal_tools(
        [TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(tool_history=tuple(_history('INV-1', 'INV-2'))),
    ) == [TRAVEL_RECORD_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]


def test_expense_guard_completion_recovery_selects_pending_invoice_then_proposal():
    provider = ExpenseGuard()
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


def test_expense_guard_completion_recovery_stays_fail_closed_without_selected_trip():
    assert ExpenseGuard().recover_completion_decision(
        _finish_decision(),
        [RAG_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME],
        _context(tool_history=tuple()),
        'expense_proposal_missing',
    ) is None


def test_expense_guard_completion_recovery_handles_single_invoice():
    provider = ExpenseGuard()
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
    provider = ExpenseGuard()
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
        LeaveGuard().validate_completion(
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

    LeaveGuard().validate_completion(
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
        ExpenseGuard().validate_completion(
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

    ExpenseGuard().validate_completion(
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

    assert ExpenseGuard().terminal_clarification(
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

    assert ExpenseGuard().terminal_clarification(
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

    assert ExpenseGuard().terminal_clarification(
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
        ExpenseGuard().validate_completion(
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

    ExpenseGuard().validate_completion(
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
        ExpenseGuard().validate_completion(
            _finish_decision(),
            tools,
            DomainContext(
                question=QUESTION,
                request_expense_reason='客户拜访',
                tool_history=history,
            ),
        )


def test_expense_completion_contract_warns_when_prerequisite_tools_are_visible():
    contract = ExpenseGuard().completion_contract([
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
        DOMAIN_PROVIDER_REGISTRY.validate_completion_for_workflow(
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

    DOMAIN_PROVIDER_REGISTRY.validate_completion_for_workflow(
        _finish_decision(), [LEAVE_BALANCE_TOOL_NAME],
        DomainContext(question='请查询我的年假余额', tool_history=history),
    )


def test_platform_completion_guard_allows_business_negative_facts():
    DOMAIN_PROVIDER_REGISTRY.validate_completion_for_workflow(
        _finish_decision(), [INVOICE_VERIFY_TOOL_NAME],
        DomainContext(
            question='请校验发票 INV-1',
            tool_history=(_completion_item(INVOICE_VERIFY_TOOL_NAME, {
                'success': True, 'invoice_id': 'INV-1', 'valid': False,
            }),),
        ),
    )

def test_platform_completion_guard_allows_rag_success():
    DOMAIN_PROVIDER_REGISTRY.validate_completion_for_workflow(
        _finish_decision(), [RAG_TOOL_NAME],
        DomainContext(
            question='公司的年假制度是什么',
            tool_history=(_completion_item(RAG_TOOL_NAME, {
                'success': True, 'answer': '年假制度。',
            }),),
        ),
    )


def test_leave_continuation_is_scoped_to_active_leave_state_and_matching_slot_input():
    state = {
        'continuation_type': 'leave_clarification',
        'start_date': '2026-07-17',
        'end_date': '2026-07-17',
        'half_day': 'NONE',
        'reason': None,
        'waiting_for': 'reason',
        'missing_fields': ['reason'],
    }
    memory = {
        'taskType': 'LEAVE_REQUEST',
        'status': 'ACTIVE',
        'taskStateJson': json.dumps({'phase': 'clarify', **state}, ensure_ascii=False),
    }
    provider = LeaveGuard()

    assert provider.continuation_state(
        DomainContext(question='家里有事', memory_context=memory)
    ) == state
    assert provider.continuation_state(
        DomainContext(question='公司的年假制度是什么', memory_context=memory)
    ) is None
    assert provider.continuation_state(
        DomainContext(question='帮我申请明天一天年假', memory_context=memory)
    ) is None
    assert provider.continuation_state(
        DomainContext(question='取消', memory_context=memory)
    ) is None
    assert provider.continuation_state(DomainContext(
        question='家里有事',
        memory_context={
            'taskType': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'taskStateJson': json.dumps(state, ensure_ascii=False),
        },
    )) is None
    assert provider.continuation_state(
        DomainContext(
            question='家里有事',
            memory_context={
                'taskType': 'LEAVE_REQUEST',
                'status': 'ABANDONED',
                'taskStateJson': json.dumps(state, ensure_ascii=False),
            },
        )
    ) is None


def test_provider_does_not_mutate_request_state():
    history = _history('INV-1')
    before = copy.deepcopy(history)
    context = _context(tool_history=tuple(history))
    ExpenseGuard().legal_tools(
        [RAG_TOOL_NAME, TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME,
         EXPENSE_PROPOSAL_TOOL_NAME],
        context,
    )
    assert history == before
    assert context.tool_history == tuple(history)


def test_capability_and_budget_values_remain_frozen():
    assert MAX_PLANNER_STEPS == 6
    assert MAX_TOOL_CALLS == 5
    assert authorized_tools(
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=True,
        java_base_url='',
        java_internal_token='',
    ) == [RAG_TOOL_NAME, LEAVE_PROPOSAL_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME]
