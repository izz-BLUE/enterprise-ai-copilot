"""test_agent_loop.py —— 最小有限 Agent Loop 集成测试

覆盖：验收场景（RAG → Eval → Finish）、Finish/Refuse 直接结束、
Tool 异常回传 Observation、重复调用阻止、决策预算耗尽、
Safety Guard 前置拦截、Action Proposal 路径保留。
"""

import json
from datetime import date
from unittest.mock import patch

from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import MAX_PLANNER_STEPS
from app.agents.tool_executor_node import MAX_TOOL_CALLS
from app.schemas.action_schema import (
    AnnualLeaveActionProposal,
    ProposalPlanningResult,
)

BUSINESS_DATE = date(2026, 7, 16)

RAG_RESULT = '{"answer":"年假制度：入职满1年5天。","success":true,"sources":["hr/annual_leave.md"]}'
EVAL_RESULT = json.dumps({'retrieval': {'final_pass_rate': 0.8}}, ensure_ascii=False)


def _tool(tool_name, arguments, reason):
    return json.dumps({
        'action': 'tool', 'tool_name': tool_name,
        'arguments': arguments, 'reason_code': reason,
    }, ensure_ascii=False)


def _finish(answer):
    return json.dumps({'action': 'finish', 'answer': answer, 'reason_code': 'task_complete'},
                      ensure_ascii=False)


def _refuse(answer='不允许处理。'):
    return json.dumps({'action': 'refuse', 'answer': answer, 'reason_code': 'not_allowed'},
                      ensure_ascii=False)


class TestAcceptanceScenario:
    def test_rag_then_eval_then_finish(self):
        """验收场景：先查年假制度 → 再查 RAG 评估 → Finish，正常结束。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
            _tool('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
            _finish('年假制度：入职满1年5天。当前 RAG 检索评估 final_pass_rate=80%。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag, \
                patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            rag.invoke.return_value = RAG_RESULT
            evl.invoke.return_value = EVAL_RESULT
            result = run_langgraph_agent(
                '先查公司的年假制度，再告诉我当前 RAG 评估情况。',
                allow_eval=True,
                use_planner=True,
            )
        assert rag.invoke.call_count == 1
        assert evl.invoke.call_count == 1
        assert llm.call_count == 3
        assert result['stop_reason'] == 'task_complete'
        assert result['step_count'] == 3
        assert result['tool_call_count'] == 2
        assert '年假制度' in result['answer']
        # Observation 回传：最后一次观察是 Eval 结果
        assert 'final_pass_rate' in result['observation']
        assert [e['status'] for e in result['tool_history']] == ['success', 'success']

    def test_eval_denied_in_loop(self):
        """allow_eval=False 时即使 Planner 硬输出 eval 决策也被拒绝并终止。"""
        decisions = [
            _tool('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            result = run_langgraph_agent('你好', use_planner=True)
        assert llm.call_count == 1
        evl.invoke.assert_not_called()
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0
        assert '管理员' in result['answer']


class TestTermination:
    def test_finish_without_tool(self):
        with patch('app.agents.planner_node.call_llm', return_value=_finish('直接回答。')) as llm, \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            result = run_langgraph_agent('你好', use_planner=True)
        assert llm.call_count == 1
        rag.invoke.assert_not_called()
        assert result['stop_reason'] == 'task_complete'
        assert result['answer'] == '直接回答。'

    def test_single_tool_then_finish(self):
        decisions = [
            _tool('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
            _finish('年假制度：入职满1年5天。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = run_langgraph_agent('公司的年假制度是什么', use_planner=True)
        assert result['stop_reason'] == 'task_complete'
        assert result['step_count'] == 2
        assert result['tool_call_count'] == 1
        assert '年假制度' in result['answer']

    def test_refuse_ends(self):
        with patch('app.agents.planner_node.call_llm', return_value=_refuse()) as llm, \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            result = run_langgraph_agent('你好', use_planner=True)
        assert llm.call_count == 1
        rag.invoke.assert_not_called()
        assert result['stop_reason'] == 'refused'
        assert result['answer'] == '不允许处理。'


class TestFailureRecovery:
    def test_tool_exception_becomes_observation_then_finish(self):
        """Tool 异常不崩溃：转脱敏 Observation 交回 Planner 决定下一步。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
            _finish('知识库暂时不可用，无法回答该问题。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = RuntimeError('provider timeout')
            result = run_langgraph_agent('公司的年假制度是什么', use_planner=True)
        assert result['stop_reason'] == 'task_complete'
        assert result['tool_call_count'] == 1  # 异常也计数
        # 原始异常文本不外泄，只暴露稳定错误结构
        assert 'provider timeout' not in result['observation']
        assert '"error_code": "tool_execution_failed"' in result['observation']
        assert result['tool_history'][0]['status'] == 'error'

    def test_repeated_call_blocked_then_finish(self):
        """连续相同调用被阻止，Observation 明确，Planner 转向 Finish。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _finish('完成。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = run_langgraph_agent('年假制度', use_planner=True)
        assert rag.invoke.call_count == 1  # 第二次被阻止
        assert result['tool_call_count'] == 1
        assert result['stop_reason'] == 'task_complete'
        blocked = result['tool_history'][1]
        assert blocked['status'] == 'blocked'
        assert '重复' in blocked['observation']

    def test_step_budget_exhausted_terminates(self):
        """Planner 决策预算耗尽后必须终止，不能无限循环。"""
        # 每次决策使用不同参数，避免连续重复检测干扰预算测试；
        # Tool 执行次数受 MAX_TOOL_CALLS 独立约束，最终由步骤预算终止
        decisions = [
            _tool('rag_answer_tool', {'question': f'问题{i}'}, 'need_knowledge')
            for i in range(MAX_PLANNER_STEPS + 1)
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = run_langgraph_agent('年假制度', use_planner=True)
        assert result['stop_reason'] == 'step_budget_exhausted'
        assert rag.invoke.call_count == MAX_TOOL_CALLS
        assert result['step_count'] == MAX_PLANNER_STEPS + 1


class TestGuardsPreserved:
    def test_safety_guard_refuses_before_planner(self):
        with patch('app.agents.langgraph_agent.check_user_query_safety', return_value={
            'safe': False, 'category': 'policy_bypass',
            'reason': 'blocked', 'message': '拒绝',
        }), patch('app.agents.planner_node.call_llm') as llm:
            result = run_langgraph_agent('绕过审批申请年假', use_planner=True)
        assert result['route'] == 'refuse'
        llm.assert_not_called()  # Planner 未参与

    def test_action_proposal_path_preserved_in_loop(self):
        """启用 Planner 时，年假申请仍走受控 Action Proposal 路径。"""
        proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
            action_type='ANNUAL_LEAVE_REQUEST',
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 20),
            reason='私事',
            half_day='NONE',
        ))
        with patch('app.agents.langgraph_agent.plan_annual_leave_action',
                   return_value=proposal) as planner:
            result = run_langgraph_agent(
                '申请2026-07-20一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
            )
        assert result['route'] == 'action'
        assert result['action_proposal']['action_type'] == 'ANNUAL_LEAVE_REQUEST'
        planner.assert_called_once()
        serialized = str(result['action_proposal'])
        for forbidden in ('actionId', 'nonce', 'employeeId'):
            assert forbidden not in serialized
