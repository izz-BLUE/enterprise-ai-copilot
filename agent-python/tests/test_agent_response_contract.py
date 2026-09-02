"""test_agent_response_contract.py —— Planner-first 公共响应契约收敛测试

仅 use_planner=True 生效：
  1. safe=False → route=refuse，保留 Safety 的 category/reason。
  2. stop_reason=task_complete：
     - 最后成功 Tool=leave_proposal_tool → route=action, category=business_action
     - 实际执行 Tool 全部是 rag_answer_tool → route=rag
     - 实际执行 Tool 全部是 eval_report_tool → route=eval
     - 无 Tool / 企业只读 Tool / 混合 Tool → route=agent, category=normal
  3. stop_reason=refused → route=refuse, category=normal。
  4. stop_reason=not_allowed → route=refuse；category 不再由 reason_code 推导：
     - Eval 权限边界 → access_control
     - Proposal 权限 / 业务日期边界 → business_action
     category 由程序层（planner_node / tool_executor_node）显式写入，
     response finalizer 只负责保留 / 兜底。
  5. provider_error / invalid_decision / step_budget_exhausted /
     未识别 stop_reason → route=error, category=error。
  6. use_planner=False 时旧确定性 Graph 行为不变。
"""

import json
from datetime import date
from unittest.mock import Mock, patch

from app.agents.langgraph_agent import run_langgraph_agent

BUSINESS_DATE = date(2026, 8, 18)


# ---------- helpers ----------

def _tool_payload(tool_name, arguments, reason_code='need_knowledge'):
    return json.dumps({
        'action': 'tool', 'tool_name': tool_name,
        'arguments': arguments, 'reason_code': reason_code,
    }, ensure_ascii=False)


def _finish_payload(answer, reason_code='task_complete'):
    return json.dumps({
        'action': 'finish', 'answer': answer, 'reason_code': reason_code,
    }, ensure_ascii=False)


def _refuse_payload(answer, reason_code='not_allowed'):
    return json.dumps({
        'action': 'refuse', 'answer': answer, 'reason_code': reason_code,
    }, ensure_ascii=False)


def _proposal_payload(start, end, reason='私事'):
    return json.dumps({
        'kind': 'proposal',
        'action_proposal': {
            'action_type': 'ANNUAL_LEAVE_REQUEST',
            'start_date': start,
            'end_date': end,
            'reason': reason,
            'half_day': 'NONE',
        },
        'missing_fields': [],
        'message': '已生成年假申请草稿，请确认后提交。',
    }, ensure_ascii=False)


# ---------- safe=False → refuse ----------

class TestSafetyPreRefuse:
    def test_safety_pre_refuse_keeps_category_and_reason(self):
        """Safety 前置拦截：route=refuse，保留 Safety 写入的 category/reason。"""
        with patch('app.agents.langgraph_agent.check_user_query_safety', return_value={
            'safe': False, 'category': 'illegal_or_policy_violation',
            'reason': '检测到高风险关键词「伪造」', 'message': '拒绝',
        }), patch('app.agents.planner_node.call_llm') as llm:
            result = run_langgraph_agent('绕过审批申请年假', use_planner=True)
        llm.assert_not_called()
        assert result['route'] == 'refuse'
        assert result['category'] == 'illegal_or_policy_violation'
        assert result['reason'] == '检测到高风险关键词「伪造」'
        assert result['safe'] is False


# ---------- task_complete 路由收敛 ----------

class TestTaskCompleteRoutes:
    def test_direct_finish_no_tool_yields_agent_route(self):
        """直接 finish（无 Tool）：route=agent, category=normal。"""
        with patch('app.agents.planner_node.call_llm',
                   return_value=_finish_payload('直接回答。')):
            result = run_langgraph_agent('你好', use_planner=True)
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'agent'
        assert result['category'] == 'normal'
        assert result['reason'] == ''

    def test_single_rag_tool_yields_rag_route(self):
        """实际执行 Tool 全部是 rag_answer_tool → route=rag。"""
        rag_payload = json.dumps(
            {"answer": "年假制度：入职满1年5天。", "success": True, "sources": []},
            ensure_ascii=False,
        )
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool_payload('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _finish_payload('年假制度：入职满1年5天。'),
        ]), patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock):
            result = run_langgraph_agent('年假制度是什么', use_planner=True)
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'rag'
        assert result['category'] == 'normal'
        assert result['reason'] == ''

    def test_single_eval_tool_yields_eval_route(self):
        """实际执行 Tool 全部是 eval_report_tool → route=eval。"""
        eval_payload = json.dumps({'retrieval': {'final_pass_rate': 0.9}})
        eval_mock = Mock()
        eval_mock.invoke.return_value = eval_payload
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool_payload('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
            _finish_payload('评估完成。'),
        ]), patch('app.agents.tool_executor_node.eval_report_tool', eval_mock):
            result = run_langgraph_agent('评估情况', use_planner=True, allow_eval=True)
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'eval'
        assert result['category'] == 'normal'

    def test_mixed_rag_and_eval_yields_agent_route(self):
        """混合 Tool（rag + eval）→ route=agent, category=normal。"""
        rag_payload = json.dumps({"answer": "ok", "success": True, "sources": []})
        eval_payload = json.dumps({'retrieval': {'final_pass_rate': 0.9}})
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        eval_mock = Mock()
        eval_mock.invoke.return_value = eval_payload
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool_payload('rag_answer_tool', {'question': 'A'}, 'need_knowledge'),
            _tool_payload('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
            _finish_payload('完成。'),
        ]), patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock), \
             patch('app.agents.tool_executor_node.eval_report_tool', eval_mock):
            result = run_langgraph_agent('先查知识再查评估', use_planner=True, allow_eval=True)
        assert result['route'] == 'agent'
        assert result['category'] == 'normal'

    def test_enterprise_only_tool_yields_agent_route(self):
        """仅企业只读 Tool（leave_balance）→ route=agent（混合 Tool 边界）。"""
        balance_payload = json.dumps({'success': True, 'data': {'remaining_days': 5}})
        balance_mock = Mock()
        balance_mock.invoke.return_value = balance_payload
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool_payload('leave_balance_tool', {}, 'need_balance'),
            _finish_payload('余额5天。'),
        ]), patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
             patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
             patch('app.agents.tool_executor_node.leave_balance_tool', balance_mock):
            result = run_langgraph_agent(
                '查一下我的年假余额',
                use_planner=True,
                employee_id='E1001',
            )
        assert result['route'] == 'agent'
        assert result['category'] == 'normal'

    def test_proposal_tool_finish_yields_action_route_business_action(self):
        """最后成功 Tool=leave_proposal_tool → route=action, category=business_action。"""
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = _proposal_payload('2026-09-01', '2026-09-01')
        with patch('app.agents.planner_node.call_llm', side_effect=[
            _tool_payload('leave_proposal_tool', {}, 'need_proposal'),
            _finish_payload('已生成草稿。'),
        ]), patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                '申请2026-09-01一天年假',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'action'
        assert result['category'] == 'business_action'


# ---------- refused ----------

class TestRefused:
    def test_refused_yields_refuse_route_normal(self):
        """stop_reason=refused → route=refuse, category=normal。"""
        with patch('app.agents.planner_node.call_llm',
                   return_value=_refuse_payload('无法处理。', 'cannot_complete')):
            result = run_langgraph_agent('拒绝', use_planner=True)
        assert result['stop_reason'] == 'refused'
        assert result['route'] == 'refuse'
        assert result['category'] == 'normal'


# ---------- not_allowed ----------

class TestNotAllowed:
    def test_hidden_eval_tool_decision_yields_invalid_decision_error(self):
        """reason_code=not_allowed → category=access_control, route=refuse。"""
        # Planner 输出不可见 eval_report_tool 时，Capability Gate post-validation
        # 以 invalid_decision 终止，而不是伪装成管理员权限拒绝。
        with patch('app.agents.planner_node.call_llm',
                   return_value=_tool_payload(
                       'eval_report_tool', {'report_type': 'all'}, 'need_eval')):
            result = run_langgraph_agent('给我看评估', use_planner=True)
        assert result['stop_reason'] == 'invalid_decision'
        assert result['route'] == 'error'
        assert result['category'] == 'error'

    def test_not_allowed_cannot_complete_yields_business_action(self):
        """reason_code=cannot_complete（业务日期缺失）→ category=business_action。"""
        with patch('app.agents.planner_node.call_llm',
                   return_value=_tool_payload(
                       'leave_proposal_tool', {}, 'need_proposal')):
            result = run_langgraph_agent(
                '申请年假',
                allow_business_actions=True,
                business_date=None,  # 业务日期缺失
                use_planner=True,
                employee_id='E1001',
            )
        assert result['stop_reason'] == 'not_allowed'
        assert result['route'] == 'refuse'
        assert result['category'] == 'business_action'

    def test_unauthorized_business_intent_is_legal_refusal_contract(self):
        """未授权业务 intent 在 Planner 前确定性返回合法拒绝。"""
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = _proposal_payload('2026-09-01', '2026-09-01')
        with patch('app.agents.planner_node.call_llm',
                   return_value=_tool_payload(
                       'leave_proposal_tool', {}, 'need_proposal')) as llm, \
             patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                '申请2026-09-01一天年假，原因为私事',
                allow_business_actions=False,  # 关键：业务动作未授权
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )
        assert result['stop_reason'] == 'not_allowed'
        assert result['route'] == 'refuse'
        assert result['category'] == 'business_action'
        assert llm.call_count == 0
        # 受控链路未被触发，action_proposal 必须清空
        proposal_mock.invoke.assert_not_called()
        assert result['action_proposal'] is None


# ---------- 技术 / 规划失败 ----------

class TestTechnicalErrors:
    def test_provider_error_yields_error_route(self):
        """LLM Provider 异常 → route=error, category=error。"""
        from app.services.llm_service import LLMProviderError
        with patch('app.agents.planner_node.call_llm',
                   side_effect=LLMProviderError('provider_unavailable', 'provider down')):
            result = run_langgraph_agent('你好', use_planner=True)
        assert result['stop_reason'] == 'provider_error'
        assert result['route'] == 'error'
        assert result['category'] == 'error'
        assert result['reason'] == ''

    def test_invalid_decision_yields_error_route(self):
        """LLM 输出非法 JSON → invalid_decision → route=error, category=error。"""
        with patch('app.agents.planner_node.call_llm', return_value='not a json'):
            result = run_langgraph_agent('你好', use_planner=True)
        assert result['stop_reason'] == 'invalid_decision'
        assert result['route'] == 'error'
        assert result['category'] == 'error'

    def test_step_budget_exhausted_yields_error_route(self):
        """步骤预算耗尽 → route=error, category=error。"""
        from app.agents.planner_node import MAX_PLANNER_STEPS
        from app.agents.tool_executor_node import MAX_TOOL_CALLS
        decisions = [
            _tool_payload('rag_answer_tool', {'question': f'q{i}'}, 'need_knowledge')
            for i in range(MAX_PLANNER_STEPS + 1)
        ]
        rag_payload = json.dumps({"answer": "ok", "success": True, "sources": []})
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
             patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock):
            result = run_langgraph_agent('反复重试', use_planner=True)
        assert result['stop_reason'] == 'step_budget_exhausted'
        assert result['route'] == 'error'
        assert result['category'] == 'error'
        # 实际 Tool 真正走 success 路径（不是假绿 error 路径）：
        # rag_mock.invoke 被调用了 MAX_TOOL_CALLS 次且全部 status=success，
        # 后续预算拦截才会产生 blocked。
        assert rag_mock.invoke.call_count == MAX_TOOL_CALLS
        for entry in result['tool_history'][:MAX_TOOL_CALLS]:
            assert entry['status'] == 'success'


# ---------- use_planner=False legacy Graph 不变 ----------

class TestLegacyGraphUnchanged:
    """use_planner=False 时：旧确定性 Graph 行为不变。"""

    def test_legacy_rag_route_preserved(self):
        rag_payload = json.dumps({"answer": "规定", "success": True, "sources": []})
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload

        def _fake_planner(_state):
            raise AssertionError('planner must not run in deterministic path')

        with patch('app.agents.langgraph_agent.rewrite_query', return_value={
            "rewritten_query": "年假政策",
            "rewrite_applied": False,
            "rewrite_reason": "",
        }), patch('app.agents.langgraph_agent.rag_answer_tool', rag_mock), \
             patch('app.agents.langgraph_agent.planner_node', side_effect=_fake_planner):
            result = run_langgraph_agent(
                '公司的年假政策是什么', use_planner=False,
            )
        assert result['route'] == 'rag'
        assert result['category'] == 'normal'

    def test_legacy_safety_refuse_route_preserved(self):
        with patch('app.agents.langgraph_agent.check_user_query_safety', return_value={
            'safe': False, 'category': 'illegal_or_policy_violation',
            'reason': 'blocked', 'message': '拒绝',
        }), patch('app.agents.langgraph_agent.planner_node') as planner:
            result = run_langgraph_agent('绕过审批', use_planner=False)
        planner.assert_not_called()
        assert result['route'] == 'refuse'
        assert result['category'] == 'illegal_or_policy_violation'

    def test_legacy_action_route_preserved(self):
        from app.schemas.action_schema import (
            AnnualLeaveActionProposal,
            ProposalPlanningResult,
        )
        proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
            action_type='ANNUAL_LEAVE_REQUEST',
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
            reason='私事',
            half_day='NONE',
        ))

        def _fake_planner(_state):
            raise AssertionError('planner must not run in deterministic path')

        with patch('app.agents.langgraph_agent.plan_annual_leave_action',
                   return_value=proposal), \
             patch('app.agents.langgraph_agent.planner_node', side_effect=_fake_planner):
            result = run_langgraph_agent(
                '申请2026-09-01一天年假',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=False,
                employee_id='E1001',
            )
        assert result['route'] == 'action'
        assert result['category'] == 'business_action'
