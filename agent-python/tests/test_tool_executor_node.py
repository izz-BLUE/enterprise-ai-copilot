"""test_tool_executor_node.py —— Tool Executor 节点测试

覆盖：RAG 调用、Eval 权限、Tool 成功、Tool 异常、预算耗尽、重复调用、
结构再校验、系统字段注入、异常信息脱敏。
"""

import json
from unittest.mock import patch

from app.agents.tool_executor_node import MAX_TOOL_CALLS
from app.agents.tool_executor_node import tool_executor_node as _tool_executor_node
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    RAG_TOOL_NAME,
)
from app.tools.enterprise_tools import leave_balance_tool as real_leave_balance_tool
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state


def state(**changes):
    value = {
        'question': '公司的年假制度是什么',
        'safe': True,
        'route': '',
        'answer': '',
        'tool_result': {},
        'sources': [],
        'reason': '',
        'category': '',
        'allow_eval': False,
        'allow_business_actions': False,
        'business_date': None,
        'trace_id': 'trace-exec',
        'employee_id': '',
        'action_proposal': None,
        'missing_fields': [],
        'step_count': 0,
        'tool_call_count': 0,
        'tool_history': [],
        'observation': '',
        'planner_decision': {
            'action': 'tool',
            'tool_name': RAG_TOOL_NAME,
            'arguments': {'question': '公司的年假制度是什么'},
            'answer': None,
            'reason_code': 'need_knowledge',
        },
        'stop_reason': '',
    }
    value.update(changes)
    return value


def tool_executor_node(value, runtime=None):
    if runtime is None:
        runtime = runtime_for_state(value)
        value = checkpoint_safe_state(value)
    return _tool_executor_node(value, runtime)


def _tool_decision(tool_name, arguments, **changes):
    default_reason = {
        RAG_TOOL_NAME: 'need_knowledge',
        EVAL_TOOL_NAME: 'need_eval',
        LEAVE_BALANCE_TOOL_NAME: 'need_balance',
        LEAVE_REQUEST_TOOL_NAME: 'need_leave_history',
        LEAVE_PROPOSAL_TOOL_NAME: 'need_proposal',
    }
    decision = {
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'answer': None,
        'reason_code': default_reason.get(tool_name, 'need_knowledge'),
    }
    decision.update(changes)
    return decision


RAG_RESULT = '{"answer":"年假制度：入职满1年5天。","success":true,"sources":["hr/annual_leave.md"]}'


class TestToolExecution:
    def test_rag_tool_success(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = tool_executor_node(state())
        assert result['tool_call_count'] == 1
        assert result['stop_reason'] == 'tool_executed'
        assert result['observation'] == RAG_RESULT
        entry = result['tool_history'][0]
        assert entry['tool_name'] == RAG_TOOL_NAME
        assert entry['status'] == 'success'
        assert entry['observation'] == RAG_RESULT
        # 系统字段由 Executor 从 AgentState 注入，不经过模型
        invoked_args = rag.invoke.call_args.args[0]
        assert invoked_args['original_question'] == '公司的年假制度是什么'
        assert invoked_args['trace_id'] == 'trace-exec'

    def test_eval_tool_success_with_allow_eval(self):
        with patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            evl.invoke.return_value = '{"retrieval":{"final_pass_rate":0.8}}'
            result = tool_executor_node(state(
                allow_eval=True,
                planner_decision=_tool_decision(EVAL_TOOL_NAME, {'report_type': 'all'}),
            ))
        assert result['tool_call_count'] == 1
        assert result['stop_reason'] == 'tool_executed'
        assert evl.invoke.call_args.args[0]['report_type'] == 'all'
        assert result['tool_history'][0]['status'] == 'success'

    def test_tool_exception_becomes_observation_and_counts(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = RuntimeError('provider timeout')
            result = tool_executor_node(state())
        # 异常也消耗一次调用预算
        assert result['tool_call_count'] == 1
        assert result['stop_reason'] == 'tool_executed'
        entry = result['tool_history'][0]
        assert entry['status'] == 'error'
        # 只暴露稳定错误结构，不含原始异常文本
        assert 'provider timeout' not in result['observation']
        parsed = json.loads(result['observation'])
        assert parsed['error_code'] == 'tool_execution_failed'
        assert entry['tool_name'] == RAG_TOOL_NAME


class TestPreExecutionGuards:
    def test_eval_tool_denied_without_allow_eval(self):
        with patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            result = tool_executor_node(state(
                planner_decision=_tool_decision(EVAL_TOOL_NAME, {'report_type': 'all'}),
            ))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0  # 未发起执行不计数
        evl.invoke.assert_not_called()
        assert result['tool_history'][0]['status'] == 'blocked'
        assert '管理员权限' in result['observation']

    def test_arguments_with_system_fields_rejected_again(self):
        result = tool_executor_node(state(
            planner_decision=_tool_decision(
                RAG_TOOL_NAME, {'question': 'x', 'trace_id': 'fake'},
            ),
        ))
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0

    def test_non_tool_decision_rejected(self):
        result = tool_executor_node(state(
            planner_decision={'action': 'finish', 'answer': 'ok', 'reason_code': 'task_complete'},
        ))
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0

    def test_missing_decision_rejected(self):
        result = tool_executor_node(state(planner_decision=None))
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0

    def test_tool_call_budget_exhausted_blocks(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            result = tool_executor_node(state(tool_call_count=MAX_TOOL_CALLS))
        assert result['stop_reason'] == 'tool_call_budget_exhausted'
        assert result['tool_call_count'] == MAX_TOOL_CALLS
        rag.invoke.assert_not_called()


class TestExceptionSanitization:
    """异常信息脱敏：完整异常只进内部日志，Planner 只见稳定错误结构。"""

    def test_sensitive_exception_text_never_leaks(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = ValueError('连接失败 DB_PASSWORD=secret123 host=10.0.0.1')
            result = tool_executor_node(state())
        # 计数行为不变：异常也消耗一次调用预算
        assert result['tool_call_count'] == 1
        assert result['stop_reason'] == 'tool_executed'
        parsed = json.loads(result['observation'])
        assert parsed['status'] == 'error'
        assert parsed['error_code'] == 'tool_execution_failed'
        assert parsed['message'] == '工具执行失败，已终止本次调用。'
        # observation 与 tool_history 均不包含原始异常内容
        raw_observation = result['observation']
        for secret in ('secret123', '10.0.0.1', 'DB_PASSWORD', '连接失败'):
            assert secret not in raw_observation
        assert 'secret123' not in json.dumps(result['tool_history'], ensure_ascii=False)

    def test_timeout_exception_maps_to_tool_timeout(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = TimeoutError('request timed out after 30s')
            result = tool_executor_node(state())
        parsed = json.loads(result['observation'])
        assert parsed['error_code'] == 'tool_timeout'
        assert parsed['message'] == '工具执行超时，已终止本次调用。'
        assert '30s' not in result['observation']

    def test_unknown_exception_maps_to_tool_execution_failed(self):
        with patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            evl.invoke.side_effect = Exception('boom')
            result = tool_executor_node(state(
                allow_eval=True,
                planner_decision=_tool_decision(EVAL_TOOL_NAME, {'report_type': 'all'}),
            ))
        parsed = json.loads(result['observation'])
        assert parsed['error_code'] == 'tool_execution_failed'
        assert parsed['tool_name'] == EVAL_TOOL_NAME
        assert 'boom' not in result['observation']


class TestSuccessDedup:
    """成功签名去重：相同 tool + 相同 arguments 且已成功 → 阻止；否则允许。"""

    def test_same_tool_and_arguments_blocked(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            first = tool_executor_node(state())
            second = tool_executor_node(state(
                tool_call_count=first['tool_call_count'],
                tool_history=first['tool_history'],
            ))
        assert second['stop_reason'] == 'already_completed'
        assert second['tool_call_count'] == 1  # 未增加
        rag.invoke.assert_called_once()
        assert '"reason": "already_completed"' in second['observation']
        assert '重复' in second['observation']
        assert second['tool_history'][1]['status'] == 'blocked'

    def test_different_arguments_not_blocked(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            first = tool_executor_node(state())
            second = tool_executor_node(state(
                tool_call_count=first['tool_call_count'],
                tool_history=first['tool_history'],
                planner_decision=_tool_decision(RAG_TOOL_NAME, {'question': '报销流程是什么'}),
            ))
        assert second['stop_reason'] == 'tool_executed'
        assert second['tool_call_count'] == 2
        rag.invoke.assert_called()

    def test_error_then_same_signature_allowed_retry(self):
        """历史为 error 的相同签名不阻止，允许合理重试。"""
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = [RuntimeError('provider timeout'), RAG_RESULT]
            first = tool_executor_node(state())
            second = tool_executor_node(state(
                tool_call_count=first['tool_call_count'],
                tool_history=first['tool_history'],
            ))
        assert first['tool_history'][0]['status'] == 'error'
        assert second['stop_reason'] == 'tool_executed'
        assert second['tool_call_count'] == 2  # 重试计数
        assert rag.invoke.call_count == 2
        assert second['tool_history'][1]['status'] == 'success'


class TestLeaveToolSystemFieldInjection:
    """只读企业 Tool：employee_id / demo_user_id / trace_id 由 Executor 注入，
    模型不得在 arguments 中夹带这些字段。"""

    def test_leave_balance_tool_injects_employee_id(self):
        with patch('app.agents.tool_executor_node.leave_balance_tool') as tool:
            tool.invoke.return_value = (
                '{"success":true,"employee_id":"DEMO-001","annual_balance":3.5}'
            )
            result = tool_executor_node(state(
                employee_id='DEMO-001',
                planner_decision=_tool_decision(LEAVE_BALANCE_TOOL_NAME, {}),
            ))
        assert result['stop_reason'] == 'tool_executed'
        assert result['tool_call_count'] == 1
        invoked = tool.invoke.call_args.args[0]
        assert invoked['employee_id'] == 'DEMO-001'
        assert invoked['trace_id'] == 'trace-exec'

    def test_leave_balance_tool_blocks_when_employee_id_missing(self):
        with patch('app.agents.tool_executor_node.leave_balance_tool') as tool:
            result = tool_executor_node(state(
                employee_id='',
                planner_decision=_tool_decision(LEAVE_BALANCE_TOOL_NAME, {}),
            ))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0
        assert result['category'] == 'access_control'
        tool.invoke.assert_not_called()
        observation = json.loads(result['observation'])
        assert observation['status'] == 'blocked'
        assert observation['reason'] == 'not_allowed'

    def test_leave_request_tool_blocks_when_employee_id_missing(self):
        with patch('app.agents.tool_executor_node.leave_request_tool') as tool:
            result = tool_executor_node(state(
                employee_id='',
                planner_decision=_tool_decision(
                    LEAVE_REQUEST_TOOL_NAME, {'limit': 10},
                ),
            ))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0
        assert result['category'] == 'access_control'
        tool.invoke.assert_not_called()

    def test_leave_proposal_tool_blocks_when_employee_id_missing(self):
        with patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
            result = tool_executor_node(state(
                employee_id='',
                allow_business_actions=True,
                planner_decision=_tool_decision(
                    LEAVE_PROPOSAL_TOOL_NAME, {},
                ),
            ))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0
        assert result['category'] == 'business_action'
        tool.invoke.assert_not_called()

    def test_leave_balance_tool_itself_keeps_identity_defense(self):
        raw = real_leave_balance_tool.invoke({
            'employee_id': '',
            'trace_id': 'trace-exec',
        })
        observation = json.loads(raw)
        assert observation['success'] is False
        assert observation['error_code'] == 'EMPLOYEE_ID_REQUIRED'

    def test_leave_balance_tool_rejects_system_args_in_decision(self):
        """PlannerDecision.validate_decision 已经拒绝 arguments 含 employee_id;
        Executor 收到非法决策时应直接 blocked 且不调用 Tool。"""
        with patch('app.agents.tool_executor_node.leave_balance_tool') as tool:
            result = tool_executor_node(state(
                employee_id='DEMO-001',
                planner_decision=_tool_decision(
                    LEAVE_BALANCE_TOOL_NAME,
                    {'employee_id': 'attacker'},
                ),
            ))
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0
        tool.invoke.assert_not_called()

    def test_leave_request_tool_injects_employee_id(self):
        with patch('app.agents.tool_executor_node.leave_request_tool') as tool:
            tool.invoke.return_value = (
                '{"success":true,"employee_id":"DEMO-001","total":0,"items":[]}'
            )
            result = tool_executor_node(state(
                employee_id='DEMO-001',
                planner_decision=_tool_decision(
                    LEAVE_REQUEST_TOOL_NAME,
                    {'limit': 10},
                ),
            ))
        assert result['stop_reason'] == 'tool_executed'
        invoked = tool.invoke.call_args.args[0]
        assert invoked['employee_id'] == 'DEMO-001'
        assert invoked['trace_id'] == 'trace-exec'
        # LLM 入参原样保留
        assert invoked['limit'] == 10

    def test_leave_request_tool_rejects_demo_user_id_arg(self):
        with patch('app.agents.tool_executor_node.leave_request_tool') as tool:
            result = tool_executor_node(state(
                employee_id='DEMO-001',
                planner_decision=_tool_decision(
                    LEAVE_REQUEST_TOOL_NAME,
                    {'status': 'all', 'limit': 10, 'demo_user_id': 'attacker'},
                ),
            ))
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0
        tool.invoke.assert_not_called()


# ---------- P2-A Expense Workflow V1：Tool Registry + Budget 5/6 ----------


class TestToolRegistryBudget:
    """ToolSpec 注册表 + MAX_TOOL_CALLS=5 / MAX_PLANNER_STEPS=6 行为不变。

    - 5 个已知 Tool 全部注册。
    - 第 6 次 Tool 调用触发 budget_exhausted，不消耗调用预算。
    - success signature 重复阻断仍按 dedup 语义工作。
    """

    def test_registry_contains_all_known_tools(self):
        from app.agents.tool_executor_node import _TOOL_REGISTRY
        from app.schemas.planner_schema import (
            EXPENSE_PROPOSAL_TOOL_NAME,
            EXPENSE_STATUS_TOOL_NAME,
            INVOICE_VERIFY_TOOL_NAME,
            TRAVEL_RECORD_TOOL_NAME,
        )
        names = set(_TOOL_REGISTRY.keys())
        # P2-A: travel/invoice 在 Phase 3；expense_proposal 在 Phase 7；
        # expense_status 在 Phase 8 加入 —— 4 个新 Tool 全部注册。
        assert names == {
            RAG_TOOL_NAME,
            EVAL_TOOL_NAME,
            LEAVE_BALANCE_TOOL_NAME,
            LEAVE_REQUEST_TOOL_NAME,
            LEAVE_PROPOSAL_TOOL_NAME,
            TRAVEL_RECORD_TOOL_NAME,
            INVOICE_VERIFY_TOOL_NAME,
            EXPENSE_PROPOSAL_TOOL_NAME,
            EXPENSE_STATUS_TOOL_NAME,
        }

    def test_max_tool_calls_is_five(self):
        from app.agents.tool_executor_node import MAX_TOOL_CALLS
        assert MAX_TOOL_CALLS == 5

    def test_max_planner_steps_is_six(self):
        from app.agents.planner_node import MAX_PLANNER_STEPS
        assert MAX_PLANNER_STEPS == 6

    def test_tool_call_count_remains_below_max_at_fifth_call(self):
        """第 5 次 Tool 调用应正常 success 且 tool_call_count=5。"""
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = '{"answer":"x","success":true,"sources":[]}'
            for i in range(5):
                result = tool_executor_node(state(
                    tool_call_count=i,
                    planner_decision=_tool_decision(
                        RAG_TOOL_NAME, {'question': f'q{i}'}, reason_code='need_knowledge',
                    ),
                ))
                assert result['stop_reason'] == 'tool_executed', i
        assert rag.invoke.call_count == 5

    def test_sixth_call_blocked_with_budget_exhausted(self):
        """第 6 次 Tool 调用（tool_call_count=5 时进入）应被 budget 阻断，不计数。"""
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = '{"answer":"x","success":true,"sources":[]}'
            result = tool_executor_node(state(
                tool_call_count=5,
                planner_decision=_tool_decision(
                    RAG_TOOL_NAME, {'question': 'q5'}, reason_code='need_knowledge',
                ),
            ))
        assert result['stop_reason'] == 'tool_call_budget_exhausted'
        assert result['tool_call_count'] == 5
        rag.invoke.assert_not_called()

    def test_repeated_signature_still_blocked_by_dedup(self):
        """成功签名去重独立于 budget 计数。"""
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = '{"answer":"x","success":true,"sources":[]}'
            decision = _tool_decision(RAG_TOOL_NAME, {'question': 'same'})
            first = tool_executor_node(state(planner_decision=decision))
            second = tool_executor_node(state(
                tool_call_count=first['tool_call_count'],
                tool_history=first['tool_history'],
                planner_decision=decision,
            ))
        assert first['stop_reason'] == 'tool_executed'
        assert second['stop_reason'] == 'already_completed'
        rag.invoke.assert_called_once()


# ---------- P2-A Expense Workflow V1: travel_record / invoice_verify Tool 执行链 ----------


class TestEnterpriseOaToolExecution:
    """Tool Executor + travel_record_tool / invoice_verify_tool 端到端 fake client。"""

    def _patch_fake_client(self, travel_response, invoice_response):
        from app.integrations.mcp import enterprise_oa_client as cli

        class _Fake:
            def __init__(self):
                self.travel_response = travel_response
                self.invoice_response = invoice_response
                self.travel_calls = []
                self.invoice_calls = []

            def travel_record_get(self, *, employee_id, limit=10):
                self.travel_calls.append({'employee_id': employee_id, 'limit': limit})
                return self.travel_response

            def invoice_verify(self, *, invoice_id, employee_id):
                self.invoice_calls.append({'invoice_id': invoice_id, 'employee_id': employee_id})
                return self.invoice_response

        fake = _Fake()
        cli.reset_enterprise_oa_client()
        cli._client_singleton = fake
        return fake

    def teardown_method(self):
        from app.integrations.mcp import enterprise_oa_client as cli
        cli.reset_enterprise_oa_client()

    def test_travel_record_tool_success(self):
        fake = self._patch_fake_client(
            travel_response={
                'success': True,
                'items': [
                    {
                        'trip_id': 'TRIP-1', 'employee_id': 'E10001',
                        'destination': '上海', 'start_date': '2026-08-18',
                        'end_date': '2026-08-20', 'purpose': '客户拜访',
                        'status': 'APPROVED', 'expense_documents': [
                            {'invoice_id': 'INV-001', 'category': 'HOTEL',
                             'declared_amount': 1600, 'description': '酒店'},
                        ],
                    },
                ],
            },
            invoice_response={'success': True, 'valid': True},
        )
        decision = _tool_decision(
            'travel_record_tool', {}, reason_code='need_travel_history'
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'tool_executed'
        # executor 注入了 employee_id / trace_id / limit=10 默认
        assert fake.travel_calls == [{'employee_id': 'E10001', 'limit': 10}]
        observation = json.loads(result['observation'])
        assert observation['success'] is True
        assert observation['items'][0]['trip_id'] == 'TRIP-1'

    def test_travel_record_tool_rejects_employee_id_in_arguments(self):
        """V2 §十一：LLM 不得在 arguments 中夹带 employee_id。"""
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={'success': True, 'valid': True},
        )
        # PlannerDecision validator 已先拒绝 employee_id 字段；但 Executor
        # 仍要做 system_arg_keys 校验作为兜底（防止 validator 被绕过）。
        decision = _tool_decision(
            'travel_record_tool', {'employee_id': 'attacker'}, reason_code='need_travel_history'
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        # PlannerDecision.validate_decision 阶段就会拒绝 employee_id 进入 arguments。
        assert result['stop_reason'] == 'invalid_decision'
        assert fake.travel_calls == []

    def test_travel_record_tool_requires_employee_id(self):
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={'success': True, 'valid': True},
        )
        decision = _tool_decision(
            'travel_record_tool', {}, reason_code='need_travel_history'
        )
        result = tool_executor_node(state(
            employee_id='',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'not_allowed'
        assert result['category'] == 'access_control'

    def test_invoice_verify_tool_success(self):
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={
                'success': True, 'invoice_id': 'INV-001', 'valid': True,
                'amount': 1600, 'category': 'HOTEL', 'duplicate': False,
            },
        )
        decision = _tool_decision(
            'invoice_verify_tool',
            {'invoice_id': 'INV-001'},
            reason_code='need_invoice_verify',
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'tool_executed'
        assert fake.invoice_calls == [{'invoice_id': 'INV-001', 'employee_id': 'E10001'}]
        observation = json.loads(result['observation'])
        assert observation['success'] is True
        assert observation['amount'] == 1600

    def test_invoice_verify_tool_cross_employee_ownership_reject(self):
        """Stress G 端到端：跨员工 invoice 验真 → MCP ownership reject 透传。"""
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={
                'success': False,
                'error_code': 'OA_MCP_INVOICE_OWNERSHIP',
                'message': 'invoice INV-005 不属于 employee E10001',
            },
        )
        decision = _tool_decision(
            'invoice_verify_tool',
            {'invoice_id': 'INV-005'},
            reason_code='need_invoice_verify',
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'tool_executed'
        observation = json.loads(result['observation'])
        assert observation['success'] is False
        assert observation['error_code'] == 'OA_MCP_INVOICE_OWNERSHIP'

    def test_invoice_verify_tool_rejects_employee_id_in_arguments(self):
        """V2 §十一：invoice_verify 强制 identity_required=true；Planner arguments
        中不允许 employee_id。validate_decision 阶段拒绝。"""
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={'success': True, 'valid': True},
        )
        decision = _tool_decision(
            'invoice_verify_tool',
            {'invoice_id': 'INV-001', 'employee_id': 'attacker'},
            reason_code='need_invoice_verify',
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'invalid_decision'
        assert fake.invoice_calls == []

    def test_invoice_verify_tool_empty_invoice_id_rejected(self):
        fake = self._patch_fake_client(
            travel_response={'success': True, 'items': []},
            invoice_response={'success': True, 'valid': True},
        )
        decision = _tool_decision(
            'invoice_verify_tool',
            {'invoice_id': ''},
            reason_code='need_invoice_verify',
        )
        result = tool_executor_node(state(
            employee_id='E10001',
            planner_decision=decision,
        ))
        assert result['stop_reason'] == 'invalid_decision'
        assert fake.invoice_calls == []
