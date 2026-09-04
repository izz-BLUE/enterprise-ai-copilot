"""Phase B Planner Shadow Routing tests."""

import importlib
import json
from datetime import date
from time import monotonic
from unittest.mock import patch

import pytest

from app.agents.domain_provider_registry import DOMAIN_PROVIDER_REGISTRY
from app.agents.planner_shadow_routing import run_shadow_routing
from app.agents.workflow_guard.contracts import DomainContext
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
)
from app.services.llm_service import LLMProviderError
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

planner_module = importlib.import_module('app.agents.planner_node')
shadow_module = importlib.import_module('app.agents.planner_shadow_routing')


def _state(**changes):
    value = {
        'question': '查询我的年假余额',
        'action_proposal': None,
        'request_expense_reason': None,
        'step_count': 0,
        'tool_call_count': 0,
        'tool_history': [],
        'observation': '',
        'memory_context': None,
        'execution_history': [],
        'continuation_leave_state': None,
        'employee_id': 'E10001',
        'allow_eval': False,
        'allow_business_actions': False,
        'business_date': date(2026, 9, 4),
        'trace_id': 'trace-shadow',
    }
    value.update(changes)
    return value


def _invoke(value):
    return planner_module.planner_node(
        checkpoint_safe_state(value),
        runtime_for_state(value),
    )


LEAVE_BALANCE_RAW = (
    '{"action":"tool","tool_name":"leave_balance_tool",'
    '"arguments":{},"reason_code":"need_balance"}'
)
RAG_RAW = (
    '{"action":"tool","tool_name":"rag_answer_tool",'
    '"arguments":{"question":"公司的报销流程是什么"},'
    '"reason_code":"need_knowledge"}'
)
LEAVE_REQUEST_RAW = (
    '{"action":"tool","tool_name":"leave_request_tool",'
    '"arguments":{"limit":10},"reason_code":"need_leave_history"}'
)
EXPENSE_PROPOSAL_RAW = (
    '{"action":"tool","tool_name":"expense_proposal_tool",'
    '"arguments":{},"reason_code":"need_expense_proposal"}'
)
LEAVE_PROPOSAL_RAW = (
    '{"action":"tool","tool_name":"leave_proposal_tool",'
    '"arguments":{},"reason_code":"need_proposal"}'
)
TRAVEL_RECORD_RAW = (
    '{"action":"tool","tool_name":"travel_record_tool",'
    '"arguments":{},"reason_code":"need_travel_history"}'
)
EXPENSE_STATUS_RAW = (
    '{"action":"tool","tool_name":"expense_status_tool",'
    '"arguments":{},"reason_code":"need_expense_status"}'
)


@pytest.fixture(autouse=True)
def _configured_tools(monkeypatch):
    monkeypatch.setattr(planner_module, 'JAVA_BASE_URL', 'http://java')
    monkeypatch.setattr(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal')
    monkeypatch.setenv('ENTERPRISE_OA_MCP_URL', 'http://oa-mcp')


def _telemetry_attributes(telemetry):
    return telemetry.call_args.args[0]


def test_shadow_is_default_off_and_formal_path_calls_once():
    value = _state()
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', False), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW) as formal, \
         patch.object(shadow_module, 'call_llm') as shadow:
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == LEAVE_BALANCE_TOOL_NAME
    formal.assert_called_once()
    shadow.assert_not_called()


def test_enabled_shadow_and_formal_path_share_authorized_tools():
    value = _state()
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW) as formal, \
         patch.object(shadow_module, 'call_llm', return_value=LEAVE_REQUEST_RAW) as shadow, \
         patch.object(shadow_module, 'record_routing_shadow'):
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == LEAVE_BALANCE_TOOL_NAME
    formal.assert_called_once()
    shadow.assert_called_once()
    formal_system = formal.call_args.args[0]
    shadow_system = shadow.call_args.args[0]
    assert f'- {LEAVE_REQUEST_TOOL_NAME}:' in formal_system
    assert f'- {LEAVE_REQUEST_TOOL_NAME}:' in shadow_system
    assert f'- {EXPENSE_PROPOSAL_TOOL_NAME}:' not in shadow_system
    assert formal_system == shadow_system


def test_wrong_shadow_route_disagrees_but_formal_result_is_unchanged():
    value = _state()
    original = json.loads(json.dumps(value, default=str))
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW), \
         patch.object(shadow_module, 'call_llm', return_value=RAG_RAW), \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == LEAVE_BALANCE_TOOL_NAME
    assert 'routing_shadow' not in result
    assert value == _state()
    attributes = _telemetry_attributes(telemetry)
    assert attributes['routing.legacy_tool'] == LEAVE_BALANCE_TOOL_NAME
    assert attributes['routing.shadow_tool'] == RAG_TOOL_NAME
    assert attributes['routing.disagreement'] is True
    assert json.loads(json.dumps(value, default=str)) == original


def test_shadow_can_observe_proposal_route_without_creating_action_proposal():
    value = _state(
        question='公司的报销流程是什么',
        allow_business_actions=True,
    )
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=RAG_RAW), \
         patch.object(shadow_module, 'call_llm', return_value=EXPENSE_PROPOSAL_RAW) as shadow, \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == RAG_TOOL_NAME
    assert value['action_proposal'] is None
    assert value['tool_history'] == []
    shadow.assert_called_once()
    attributes = _telemetry_attributes(telemetry)
    assert attributes['routing.shadow_tool'] == EXPENSE_PROPOSAL_TOOL_NAME
    assert attributes['routing.shadow_valid'] is True


@pytest.mark.parametrize(
    ('shadow_result', 'error_code'),
    [
        ('not json', 'invalid_json'),
        (LLMProviderError('provider_timeout', 'provider timed out'), 'provider_timeout'),
    ],
)
def test_invalid_or_provider_shadow_failure_is_fail_open(shadow_result, error_code):
    value = _state()
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW), \
         patch.object(shadow_module, 'call_llm', side_effect=shadow_result), \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == LEAVE_BALANCE_TOOL_NAME
    assert result['step_count'] == 1
    assert _telemetry_attributes(telemetry)['routing.shadow_error_code'] == error_code


def test_unregistered_for_request_shadow_tool_is_invalid_and_not_executed():
    value = _state()
    eval_raw = (
        '{"action":"tool","tool_name":"eval_report_tool",'
        '"arguments":{"report_type":"all"},"reason_code":"need_eval"}'
    )
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW), \
         patch.object(shadow_module, 'call_llm', return_value=eval_raw), \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = _invoke(value)

    assert result['planner_decision']['tool_name'] == LEAVE_BALANCE_TOOL_NAME
    attributes = _telemetry_attributes(telemetry)
    assert attributes['routing.shadow_valid'] is False
    assert attributes['routing.shadow_error_code'] == 'tool_not_authorized'


def test_active_continuation_skips_shadow_call():
    value = _state(
        question='客户拜访',
        allow_business_actions=True,
        memory_context={
            'taskType': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'taskStateJson': json.dumps({
                'waiting_for': 'reason',
                'missing_fields': ['reason'],
                'original_request': '根据最近一次已批准的出差准备差旅报销申请。',
            }),
        },
    )
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=RAG_RAW), \
         patch.object(shadow_module, 'call_llm') as shadow, \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        _invoke(value)

    shadow.assert_not_called()
    assert _telemetry_attributes(telemetry)['routing.shadow_error_code'] == (
        'active_continuation'
    )


def test_existing_action_proposal_skips_shadow_call():
    value = _state(action_proposal={'action_type': 'ANNUAL_LEAVE'})
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW), \
         patch.object(shadow_module, 'call_llm') as shadow, \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        _invoke(value)

    shadow.assert_not_called()
    assert _telemetry_attributes(telemetry)['routing.shadow_error_code'] == (
        'action_proposal_present'
    )


def test_insufficient_deadline_skips_extra_call():
    value = _state(deadline_monotonic=monotonic() + 0.5)
    with patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', True), \
         patch.object(planner_module, 'call_llm', return_value=LEAVE_BALANCE_RAW), \
         patch.object(shadow_module, 'call_llm') as shadow, \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        _invoke(value)

    shadow.assert_not_called()
    assert _telemetry_attributes(telemetry)['routing.shadow_error_code'] == (
        'deadline_insufficient'
    )


def test_expense_guard_observes_and_rejects_wrong_invoice_scope_without_execution():
    history = [{
        'tool_name': 'travel_record_tool',
        'arguments': {},
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'items': [{
                'trip_id': 'TRIP-1',
                'status': 'APPROVED',
                'expense_documents': [{'invoice_id': 'INV-1'}],
            }],
        }),
    }]
    context = DomainContext(
        question='根据 TRIP-1 验证发票 INV-999',
        tool_history=tuple(history),
        request_expense_reason='客户拜访',
    )
    invoice_raw = (
        '{"action":"tool","tool_name":"invoice_verify_tool",'
        '"arguments":{"invoice_id":"INV-999"},'
        '"reason_code":"need_invoice_verify"}'
    )
    with patch.object(shadow_module, 'call_llm', return_value=invoice_raw), \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = run_shadow_routing(
            question=context.question,
            authorized_tools=[INVOICE_VERIFY_TOOL_NAME],
            tool_history=history,
            observation='',
            steps_left=6,
            memory_context=None,
            execution_history=[],
            context=context,
            guard_for_tool=DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool,
            legacy_action='tool',
            legacy_tool=INVOICE_VERIFY_TOOL_NAME,
            timeout_seconds=3.0,
        )

    assert result.valid is True
    assert result.guard_allowed is False
    assert result.error_code == 'guard_rejected'
    assert _telemetry_attributes(telemetry)['routing.shadow_guard_allowed'] is False


def test_expense_guard_rejects_proposal_before_prerequisites_without_execution():
    history = [{
        'tool_name': 'travel_record_tool',
        'arguments': {},
        'status': 'success',
        'observation': json.dumps({
            'success': True,
            'items': [{
                'trip_id': 'TRIP-1',
                'status': 'APPROVED',
                'expense_documents': [{'invoice_id': 'INV-1'}],
            }],
        }),
    }]
    context = DomainContext(
        question='帮我报销 TRIP-1 的对应发票，原因是客户拜访',
        tool_history=tuple(history),
        request_expense_reason='客户拜访',
    )
    with patch.object(shadow_module, 'call_llm', return_value=EXPENSE_PROPOSAL_RAW), \
         patch.object(shadow_module, 'record_routing_shadow') as telemetry:
        result = run_shadow_routing(
            question=context.question,
            authorized_tools=[EXPENSE_PROPOSAL_TOOL_NAME],
            tool_history=history,
            observation='',
            steps_left=6,
            memory_context=None,
            execution_history=[],
            context=context,
            guard_for_tool=DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool,
            legacy_action='tool',
            legacy_tool=EXPENSE_PROPOSAL_TOOL_NAME,
            timeout_seconds=3.0,
        )

    assert result.valid is True
    assert result.guard_allowed is False
    assert result.error_code == 'guard_rejected'
    assert _telemetry_attributes(telemetry)['routing.shadow_guard_allowed'] is False


ROUTING_CORPUS = (
    ('查询我的年假余额', LEAVE_BALANCE_TOOL_NAME, LEAVE_BALANCE_RAW),
    ('我还剩多少年假', LEAVE_BALANCE_TOOL_NAME, LEAVE_BALANCE_RAW),
    ('年假余额怎么计算', RAG_TOOL_NAME, RAG_RAW),
    ('年假制度是什么', RAG_TOOL_NAME, RAG_RAW),
    ('查询最近请假记录', LEAVE_REQUEST_TOOL_NAME, LEAVE_REQUEST_RAW),
    ('帮我请明天一天年假', LEAVE_PROPOSAL_TOOL_NAME, LEAVE_PROPOSAL_RAW),
    ('查我的报销状态', EXPENSE_STATUS_TOOL_NAME, EXPENSE_STATUS_RAW),
    ('最近一次报销到哪了', EXPENSE_STATUS_TOOL_NAME, EXPENSE_STATUS_RAW),
    ('查询最近一次出差', TRAVEL_RECORD_TOOL_NAME, TRAVEL_RECORD_RAW),
    ('帮我报销最近一次出差', TRAVEL_RECORD_TOOL_NAME, TRAVEL_RECORD_RAW),
    ('公司的报销流程是什么', RAG_TOOL_NAME, RAG_RAW),
    ('报销需要什么材料', RAG_TOOL_NAME, RAG_RAW),
    ('查年假余额，再看看最近报销状态', RAG_TOOL_NAME, RAG_RAW),
)


@pytest.mark.parametrize(('question', 'expected_tool', 'raw'), ROUTING_CORPUS)
def test_small_shadow_routing_corpus(question, expected_tool, raw):
    context = DomainContext(question=question)
    with patch.object(shadow_module, 'call_llm', return_value=raw), \
         patch.object(shadow_module, 'record_routing_shadow'):
        result = run_shadow_routing(
            question=question,
            authorized_tools=[
                RAG_TOOL_NAME,
                LEAVE_BALANCE_TOOL_NAME,
                LEAVE_REQUEST_TOOL_NAME,
                LEAVE_PROPOSAL_TOOL_NAME,
                TRAVEL_RECORD_TOOL_NAME,
                INVOICE_VERIFY_TOOL_NAME,
                EXPENSE_PROPOSAL_TOOL_NAME,
                EXPENSE_STATUS_TOOL_NAME,
            ],
            tool_history=[],
            observation='',
            steps_left=6,
            memory_context=None,
            execution_history=[],
            context=context,
            guard_for_tool=DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool,
            legacy_action='tool',
            legacy_tool=expected_tool,
            timeout_seconds=3.0,
        )

    assert result.valid is True
    assert result.tool_name == expected_tool
