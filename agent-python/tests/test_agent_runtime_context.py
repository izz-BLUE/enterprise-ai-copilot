"""P3-0 tests for the checkpoint-safe AgentState boundary."""

import importlib
import json
from datetime import date
from unittest.mock import Mock, patch

from langgraph.runtime import Runtime

from app.agents.langgraph_agent import (
    AgentState,
    _approval_route,
    action_node,
    router_node,
)
from app.agents.planner_node import planner_node
from app.agents.runtime_context import AgentRuntimeContext
from app.agents.tool_executor_node import (
    _ExecutorContext,
    _inject_oamcp_read,
    tool_executor_node,
)
from app.schemas.planner_schema import (
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
)

_OLD_DATE = date(2026, 1, 1)
_CURRENT_DATE = date(2026, 8, 26)


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
        'step_count': 0,
        'tool_call_count': 0,
        'tool_history': [],
        'observation': '',
        'planner_decision': None,
        'stop_reason': '',
        'memory_context': None,
    }
    value.update(changes)
    return value


def _runtime(**changes) -> Runtime[AgentRuntimeContext]:
    context: AgentRuntimeContext = {
        'employee_id': '',
        'allow_eval': False,
        'allow_business_actions': False,
        'business_date': _CURRENT_DATE,
        'trace_id': 'current-trace',
        'deadline_monotonic': float('inf'),
    }
    context.update(changes)
    return Runtime(context=context)


def _decision(tool_name, arguments=None, reason_code='need_knowledge'):
    return {
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments or {},
        'answer': None,
        'reason_code': reason_code,
    }


def test_agent_state_does_not_declare_request_trusted_fields():
    fields = AgentState.__annotations__
    assert not {
        'employee_id', 'allow_eval', 'allow_business_actions',
        'business_date', 'trace_id', 'deadline_monotonic',
    }.intersection(fields)


def test_task_runtime_approval_route_ends_without_inspecting_external_state():
    runtime = _runtime(execution_mode='TASK_RUNTIME')

    # The state is deliberately not a valid confirmed Expense payload.  The
    # TASK_RUNTIME branch must still route directly to finalize; only the
    # trusted Java-injected Runtime Context selects the lifecycle.
    assert _approval_route({'hitl_result': {}}, runtime) == 'finalize_node'


def test_legacy_approval_route_keeps_external_expense_compatibility():
    runtime = _runtime(execution_mode='LEGACY_SINGLE')
    state = {
        'hitl_result': {
            'schema_version': 1,
            'wait_id': 'wait_' + 'a' * 64,
            'execution_id': 'ex_' + 'b' * 32,
            'decision': 'CONFIRMED',
            'action_id': 'act_1',
            'action_type': 'EXPENSE_CLAIM',
            'action_status': 'SUCCEEDED',
            'request_id': 'EXP-1',
            'message': '已提交。',
        },
    }
    assert _approval_route(state, runtime) == 'prepare_external_wait_node'


def test_planner_ignores_stale_state_permission():
    stale = _state(
        allow_eval=True,
        employee_id='OLD',
        business_date=_OLD_DATE,
        trace_id='old-trace',
    )
    raw = (
        '{"action":"tool","tool_name":"eval_report_tool",'
        '"arguments":{"report_type":"all"},"reason_code":"need_eval"}'
    )
    with patch('app.agents.planner_node.call_llm', return_value=raw) as llm:
        result = planner_node(stale, _runtime(
            allow_eval=False,
            employee_id='CURRENT',
            business_date=_CURRENT_DATE,
            trace_id='current-trace',
        ))
    assert result['stop_reason'] == 'invalid_decision'
    assert llm.call_count == 1


def test_planner_ignores_stale_business_action_permission_and_identity():
    stale = _state(
        question='申请2026-08-30一天年假，原因为私事',
        allow_business_actions=True,
        employee_id='OLD',
        business_date=_OLD_DATE,
    )
    raw = (
        '{"action":"tool","tool_name":"leave_proposal_tool",'
        '"arguments":{},"reason_code":"need_proposal"}'
    )
    with patch('app.agents.planner_node.call_llm', return_value=raw) as llm:
        result = planner_node(stale, _runtime(
            allow_business_actions=False,
            employee_id='CURRENT',
            business_date=_CURRENT_DATE,
        ))
    assert result['stop_reason'] == 'invalid_decision'
    assert result['planner_decision']['reason_code'] == 'cannot_complete'
    assert result.get('category') in (None, '')
    llm.assert_called_once()


def test_router_ignores_stale_state_permissions():
    stale = _state(
        question='查看评估通过率',
        allow_eval=True,
        allow_business_actions=True,
        business_date=_OLD_DATE,
    )
    result = router_node(stale, _runtime(allow_eval=False))
    assert result['route'] == 'refuse'
    assert result['category'] == 'access_control'


def test_tool_executor_injects_current_runtime_identity_and_trace():
    stale = _state(
        employee_id='OLD',
        trace_id='old-trace',
        planner_decision=_decision(LEAVE_BALANCE_TOOL_NAME, reason_code='need_balance'),
    )
    tool = Mock()
    tool.invoke.return_value = json.dumps({'success': True, 'annual_balance': 3.5})
    with patch('app.agents.tool_executor_node.leave_balance_tool', tool):
        result = tool_executor_node(stale, _runtime(
            employee_id='E10001',
            trace_id='current-trace',
        ))
    assert result['stop_reason'] == 'tool_executed'
    args = tool.invoke.call_args.args[0]
    assert args['employee_id'] == 'E10001'
    assert args['trace_id'] == 'current-trace'


def test_proposal_uses_current_runtime_business_date():
    stale = _state(
        question='申请2026-08-30一天年假，原因为私事',
        employee_id='OLD',
        allow_business_actions=True,
        business_date=_OLD_DATE,
        trace_id='old-trace',
        planner_decision=_decision(
            LEAVE_PROPOSAL_TOOL_NAME,
            reason_code='need_proposal',
        ),
    )
    tool = Mock()
    tool.invoke.return_value = json.dumps({
        'success': True,
        'kind': 'clarification',
        'action_proposal': None,
        'missing_fields': ['reason'],
    })
    with patch('app.agents.tool_executor_node.leave_proposal_tool', tool):
        result = tool_executor_node(stale, _runtime(
            employee_id='E10001',
            allow_business_actions=True,
            business_date=_CURRENT_DATE,
            trace_id='current-trace',
        ))
    assert result['stop_reason'] == 'tool_executed'
    args = tool.invoke.call_args.args[0]
    assert args['business_date'] == _CURRENT_DATE.isoformat()
    assert args['trace_id'] == 'current-trace'


def test_deterministic_action_uses_current_runtime_business_date():
    stale = _state(
        question='申请2026-08-30一天年假，原因为私事',
        allow_business_actions=True,
        business_date=_OLD_DATE,
        trace_id='old-trace',
    )
    planned = Mock()
    planned.return_value = Mock(kind='clarification', clarification=Mock(
        question='请补充信息', missing_fields=['reason'],
    ))
    with patch('app.agents.langgraph_agent.plan_annual_leave_action', planned):
        result = action_node(stale, _runtime(
            allow_business_actions=True,
            business_date=_CURRENT_DATE,
            trace_id='current-trace',
        ))
    assert result['route'] == 'action'
    assert planned.call_args.kwargs['business_date'] == _CURRENT_DATE
    assert planned.call_args.kwargs['trace_id'] == 'current-trace'


def test_planner_timeout_uses_runtime_deadline_and_skips_llm():
    stale = _state(deadline_monotonic=float('inf'))
    with patch('app.agents.planner_node.call_llm') as llm:
        result = planner_node(stale, _runtime(deadline_monotonic=0.0))
    assert result['stop_reason'] == 'request_timeout'
    llm.assert_not_called()


def test_tool_executor_timeout_uses_runtime_deadline_and_skips_tool():
    stale = _state(
        deadline_monotonic=float('inf'),
        planner_decision=_decision(RAG_TOOL_NAME),
    )
    tool = Mock()
    with patch('app.agents.tool_executor_node.rag_answer_tool', tool):
        result = tool_executor_node(stale, _runtime(deadline_monotonic=0.0))
    assert result['stop_reason'] == 'request_timeout'
    tool.invoke.assert_not_called()


def test_executor_context_owns_tool_name_without_module_global_state():
    module = importlib.import_module('app.agents.tool_executor_node')
    assert not hasattr(module, '_current_tool_name')
    ctx = _ExecutorContext(
        tool_name=TRAVEL_RECORD_TOOL_NAME,
        employee_id='E10001',
        trace_id='current-trace',
        question='q',
        business_date=_CURRENT_DATE,
    )
    args = _inject_oamcp_read({}, ctx)
    assert args['limit'] == 10
