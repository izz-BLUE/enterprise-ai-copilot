"""test_tool_executor_node.py —— Tool Executor 节点测试

覆盖：RAG 调用、Eval 权限、Tool 成功、Tool 异常、预算耗尽、重复调用、
结构再校验、系统字段注入、异常信息脱敏。
"""

import json
from unittest.mock import patch

from app.agents.tool_executor_node import MAX_TOOL_CALLS, tool_executor_node
from app.schemas.planner_schema import EVAL_TOOL_NAME, RAG_TOOL_NAME


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


def _tool_decision(tool_name, arguments, **changes):
    decision = {
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'answer': None,
        'reason_code': 'need_knowledge' if tool_name == RAG_TOOL_NAME else 'need_eval',
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


class TestRepeatedCall:
    def test_same_tool_and_arguments_blocked(self):
        with patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            first = tool_executor_node(state())
            second = tool_executor_node(state(
                tool_call_count=first['tool_call_count'],
                tool_history=first['tool_history'],
            ))
        assert second['stop_reason'] == 'repeated_call'
        assert second['tool_call_count'] == 1  # 未增加
        rag.invoke.assert_called_once()
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
