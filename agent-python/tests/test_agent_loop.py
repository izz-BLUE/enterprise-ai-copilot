"""test_agent_loop.py —— 最小有限 Agent Loop 集成测试

覆盖：验收场景（RAG → Eval → Finish）、Finish/Refuse 直接结束、
Tool 异常回传 Observation、重复调用阻止、决策预算耗尽、
Safety Guard 前置拦截、Action Proposal 路径保留。
"""

import json
from datetime import date
from unittest.mock import Mock, patch

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
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0
        assert result['route'] == 'error'


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
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = RuntimeError('provider timeout')
            result = run_langgraph_agent('公司的年假制度是什么', use_planner=True)
        assert llm.call_count == 2  # Tool error is an Observation, not Planner semantic repair.
        assert result['stop_reason'] == 'task_complete'
        assert result['tool_call_count'] == 1  # 异常也计数
        # 原始异常文本不外泄，只暴露稳定错误结构
        assert 'provider timeout' not in result['observation']
        assert '"error_code": "tool_execution_failed"' in result['observation']
        assert result['tool_history'][0]['status'] == 'error'


class TestPlannerSemanticRepair:
    """Planner 结构/语义非法时最多自修复一次，且不扩大执行权限。"""

    def test_balance_then_invalid_finish_repairs_to_leave_proposal(self, caplog):
        premature_finish = json.dumps({
            'action': 'finish',
            'answer': 'DO_NOT_ECHO',
            'reason_code': 'cannot_complete',
        }, ensure_ascii=False)
        proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
            action_type='ANNUAL_LEAVE_REQUEST',
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 20),
            reason='私事',
            half_day='NONE',
        ))
        proposal_payload = json.dumps({
            'kind': 'proposal',
            'action_proposal': proposal.proposal.model_dump(mode='json'),
            'missing_fields': [],
            'message': '已生成年假申请草稿，请确认后提交。',
        }, ensure_ascii=False)
        responses = iter([
            _tool('leave_balance_tool', {}, 'need_balance'),
            premature_finish,
            _tool('leave_proposal_tool', {}, 'need_proposal'),
            _finish('已生成年假申请草稿，请确认后提交。'),
        ])
        prompts = []

        def fake_llm(_system_prompt, user_prompt, **_kwargs):
            prompts.append(user_prompt)
            return next(responses)

        balance_tool = Mock()
        balance_tool.invoke.return_value = json.dumps({
            'success': True, 'data': {'remaining_days': 5},
        }, ensure_ascii=False)
        proposal_tool = Mock()
        proposal_tool.invoke.return_value = proposal_payload
        with patch('app.agents.planner_node.call_llm', side_effect=fake_llm) as llm, \
                patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
                patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
                patch('app.agents.tool_executor_node.leave_balance_tool', balance_tool), \
                patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
            result = run_langgraph_agent(
                '我明天请一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 4  # 1 balance + 1 invalid + 1 repair + 1 finish
        assert balance_tool.invoke.call_count == 1
        assert proposal_tool.invoke.call_count == 1
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'action'
        assert result['action_proposal']['action_type'] == 'ANNUAL_LEAVE_REQUEST'
        assert result['step_count'] == 3  # repair stays within the same Planner node
        assert '尚未完成 leave_proposal_tool' in prompts[2]
        assert 'DO_NOT_ECHO' not in prompts[2]
        assert any(
            'error_type=planner_completion_validation '
            'error_code=leave_proposal_missing' in record.message
            for record in caplog.records
        )

    def test_balance_then_reason_mismatch_premature_finish_fails_closed_after_one_repair(
            self, caplog):
        """业务完成校验优先于 finish reason 校验，且仍只修复一次。"""
        invalid_finish = json.dumps({
            'action': 'finish',
            'answer': 'not accepted',
            'reason_code': 'cannot_complete',
        }, ensure_ascii=False)
        balance_tool = Mock()
        balance_tool.invoke.return_value = json.dumps({
            'success': True, 'data': {'remaining_days': 5},
        }, ensure_ascii=False)
        proposal_tool = Mock()
        with patch('app.agents.planner_node.call_llm',
                   side_effect=[
                       _tool('leave_balance_tool', {}, 'need_balance'),
                       invalid_finish,
                       invalid_finish,
                   ]) as llm, \
                patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
                patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
                patch('app.agents.tool_executor_node.leave_balance_tool', balance_tool), \
                patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
            result = run_langgraph_agent(
                '我明天请一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 3  # balance + original decision + one repair
        assert balance_tool.invoke.call_count == 1
        proposal_tool.invoke.assert_not_called()
        assert result['stop_reason'] == 'invalid_decision'
        assert result['route'] == 'error'
        failures = [record.message for record in caplog.records
                    if 'planner semantic validation failure' in record.message]
        assert len(failures) == 2
        assert all('error_code=leave_proposal_missing' in message for message in failures)
        assert not any('finish_reason_code_mismatch' in message for message in failures)

    def test_second_invalid_decision_fails_closed_after_one_repair(self):
        invalid_finish = json.dumps({
            'action': 'finish',
            'answer': 'not accepted',
            'reason_code': 'need_balance',
        }, ensure_ascii=False)
        with patch('app.agents.planner_node.call_llm',
                   side_effect=[invalid_finish, invalid_finish]) as llm, \
                patch('app.agents.tool_executor_node.leave_balance_tool') as balance:
            result = run_langgraph_agent(
                '查一下我的年假余额',
                use_planner=True,
                employee_id='E1001',
            )

        assert llm.call_count == 2  # exactly one semantic repair attempt
        balance.invoke.assert_not_called()
        assert result['stop_reason'] == 'invalid_decision'
        assert result['route'] == 'error'

    def test_read_only_leave_balance_then_legal_finish_is_accepted(self):
        """只读年假余额查询完成后，合法 finish 不应被误判为申请未完成。"""
        decisions = [
            _tool('leave_balance_tool', {}, 'need_balance'),
            _finish('当前年假余额为 5 天。'),
        ]
        balance_tool = Mock()
        balance_tool.invoke.return_value = json.dumps({
            'success': True, 'data': {'remaining_days': 5},
        }, ensure_ascii=False)
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
                patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
                patch('app.agents.tool_executor_node.leave_balance_tool', balance_tool):
            result = run_langgraph_agent(
                '查一下我的年假余额',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 2
        assert balance_tool.invoke.call_count == 1
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'agent'

    def test_second_legal_premature_finish_fails_closed_after_one_repair(self):
        """第二次仍为合法格式但未完成 Proposal 的 finish 必须 fail-closed。"""
        premature_finish = _finish('已完成。')
        proposal_tool = Mock()
        with patch('app.agents.planner_node.call_llm',
                   side_effect=[premature_finish, premature_finish]) as llm, \
                patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
            result = run_langgraph_agent(
                '我明天请一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 2  # exactly one semantic repair attempt
        proposal_tool.invoke.assert_not_called()
        assert result['stop_reason'] == 'invalid_decision'
        assert result['route'] == 'error'

    def test_successful_proposal_with_missing_fields_allows_finish(self):
        """Proposal Tool 成功但返回 missing_fields 时，finish 仍应进入 Clarification。"""
        clarification_payload = json.dumps({
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['reason'],
            'message': '请补充申请原因。',
        }, ensure_ascii=False)
        decisions = [
            _tool('leave_proposal_tool', {}, 'need_proposal'),
            _finish('请补充申请原因。'),
        ]
        proposal_tool = Mock()
        proposal_tool.invoke.return_value = clarification_payload
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
            result = run_langgraph_agent(
                '申请明天一天年假',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 2
        assert proposal_tool.invoke.call_count == 1
        assert result['stop_reason'] == 'task_complete'
        assert result['route'] == 'action'
        assert result['action_proposal'] is None
        assert result['missing_fields'] == ['reason']

    def test_successful_proposal_runs_finish_reason_validation(self, caplog):
        """Proposal 已成功后，普通 finish reason consistency 仍保留。"""
        clarification_payload = json.dumps({
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['reason'],
            'message': '请补充申请原因。',
        }, ensure_ascii=False)
        invalid_finish = json.dumps({
            'action': 'finish',
            'answer': 'not accepted',
            'reason_code': 'cannot_complete',
        }, ensure_ascii=False)
        proposal_tool = Mock()
        proposal_tool.invoke.return_value = clarification_payload
        with patch('app.agents.planner_node.call_llm',
                   side_effect=[
                       _tool('leave_proposal_tool', {}, 'need_proposal'),
                       invalid_finish,
                       invalid_finish,
                   ]) as llm, \
                patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_tool):
            result = run_langgraph_agent(
                '申请明天一天年假',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )

        assert llm.call_count == 3
        assert proposal_tool.invoke.call_count == 1
        assert result['stop_reason'] == 'invalid_decision'
        failures = [record.message for record in caplog.records
                    if 'planner semantic validation failure' in record.message]
        assert len(failures) == 2
        assert all('error_code=finish_reason_code_mismatch' in message for message in failures)
        assert not any('leave_proposal_missing' in message for message in failures)

    def test_legal_decision_uses_one_llm_call(self):
        with patch('app.agents.planner_node.call_llm',
                   return_value=_finish('直接回答。')) as llm:
            result = run_langgraph_agent('你好', use_planner=True)

        llm.assert_called_once()
        assert result['stop_reason'] == 'task_complete'

    def test_provider_error_does_not_trigger_semantic_repair(self):
        from app.services.llm_service import LLMProviderError

        with patch('app.agents.planner_node.call_llm',
                   side_effect=LLMProviderError('provider_timeout', 'timeout')) as llm:
            result = run_langgraph_agent('你好', use_planner=True)

        llm.assert_called_once()
        assert result['stop_reason'] == 'provider_error'

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
        # 每次决策使用不同参数，避免成功签名去重干扰预算测试；
        # Tool 执行次数受 MAX_TOOL_CALLS 独立约束，最终由步骤预算终止
        decisions = [
            _tool('rag_answer_tool', {'question': f'问题{i}'}, 'need_knowledge')
            for i in range(MAX_PLANNER_STEPS + 1)
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions) as llm, \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = run_langgraph_agent('年假制度', use_planner=True)
        assert result['stop_reason'] == 'step_budget_exhausted'
        assert rag.invoke.call_count == MAX_TOOL_CALLS
        assert llm.call_count == MAX_PLANNER_STEPS  # 不越界调用第 MAX+1 次
        assert result['step_count'] == MAX_PLANNER_STEPS  # 不再 +1
        assert '预算已耗尽' in result['answer']


class TestHistoryRendering:
    """历史 Tool 结果必须真实进入 Planner Prompt（渲染键与 Executor 写入键一致）。"""

    def test_planner_sees_both_tool_results_in_history(self):
        """RAG(A) + Eval(B) 成功后，下一轮 Prompt 同时包含两个历史成功结果。"""
        captured = {}
        responses = iter([
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _tool('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
            _finish('完成。'),
        ])

        def fake_llm(system_prompt, user_prompt, **_kwargs):
            captured['user_prompt'] = user_prompt
            return next(responses)

        with patch('app.agents.planner_node.call_llm', side_effect=fake_llm), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag, \
                patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            rag.invoke.return_value = RAG_RESULT
            evl.invoke.return_value = EVAL_RESULT
            result = run_langgraph_agent(
                '先查年假制度，再查评估。',
                allow_eval=True,
                use_planner=True,
            )
        assert result['stop_reason'] == 'task_complete'
        prompt = captured['user_prompt']
        # 两个 Tool 的实际结果都进入历史渲染
        assert '年假制度：入职满1年5天。' in prompt  # RAG(A) 结果
        assert 'final_pass_rate' in prompt          # Eval(B) 结果
        assert 'status=success' in prompt
        # 历史行不再以"冒号后空"的旧格式出现（工具描述段不受影响）
        assert 'rag_answer_tool: \n' not in prompt
        assert 'eval_report_tool: \n' not in prompt


class TestSuccessDedup:
    """成功签名去重：相同 tool + 相同 arguments 且已成功 → 阻止；否则允许。"""

    def test_rag_a_then_eval_then_rag_a_blocked(self):
        """RAG(A) 成功 → Eval(B) → 再 RAG(A)：已成功签名被阻止，不计数。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
            _tool('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
            _tool('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
            _finish('完成。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag, \
                patch('app.agents.tool_executor_node.eval_report_tool') as evl:
            rag.invoke.return_value = RAG_RESULT
            evl.invoke.return_value = EVAL_RESULT
            result = run_langgraph_agent(
                '先查年假制度，再查评估，最后再确认年假制度。',
                allow_eval=True,
                use_planner=True,
            )
        assert rag.invoke.call_count == 1  # 第二次 RAG(A) 被阻止
        assert evl.invoke.call_count == 1
        assert result['tool_call_count'] == 2  # 阻止不计数
        assert result['step_count'] == 4
        assert result['stop_reason'] == 'task_complete'
        blocked = result['tool_history'][2]
        assert blocked['status'] == 'blocked'
        assert '"reason": "already_completed"' in blocked['observation']

    def test_rag_a_then_rag_c_allowed(self):
        """相同 Tool、不同 arguments → 允许。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _tool('rag_answer_tool', {'question': '报销流程'}, 'need_knowledge'),
            _finish('完成。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.return_value = RAG_RESULT
            result = run_langgraph_agent('先查年假制度，再查报销流程', use_planner=True)
        assert rag.invoke.call_count == 2
        assert result['tool_call_count'] == 2
        assert result['stop_reason'] == 'task_complete'

    def test_rag_a_error_then_rag_a_retry_allowed(self):
        """相同签名但历史为 error → 允许重试。"""
        decisions = [
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _tool('rag_answer_tool', {'question': '年假制度'}, 'need_knowledge'),
            _finish('完成。'),
        ]
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
                patch('app.agents.tool_executor_node.rag_answer_tool') as rag:
            rag.invoke.side_effect = [RuntimeError('provider timeout'), RAG_RESULT]
            result = run_langgraph_agent('年假制度', use_planner=True)
        assert rag.invoke.call_count == 2  # 失败后相同签名重试被允许
        assert result['tool_call_count'] == 2
        assert [e['status'] for e in result['tool_history']] == ['error', 'success']
        assert result['stop_reason'] == 'task_complete'


class TestGuardsPreserved:
    def test_safety_guard_refuses_before_planner(self):
        """Safety 保留 pre-Planner 拦截边界：unsafe 输入直接 END，不调用 Planner LLM。
        Composite Enterprise Task P0：Agent Loop 为 Planner-first 拓扑，
        但 unsafe 输入不得进入 Planner。
        """
        with patch('app.agents.langgraph_agent.check_user_query_safety', return_value={
            'safe': False, 'category': 'policy_bypass',
            'reason': 'blocked', 'message': '拒绝',
        }), patch('app.agents.planner_node.call_llm') as llm, \
             patch('app.agents.tool_executor_node.leave_proposal_tool') as proposal_tool:
            result = run_langgraph_agent('绕过审批申请年假', use_planner=True)
        assert result['route'] == 'refuse'
        assert result['answer'] == '拒绝'
        llm.assert_not_called()  # unsafe 输入不调用 Planner LLM
        proposal_tool.assert_not_called()  # 未进入受控链路

    def test_action_proposal_path_preserved_in_loop(self):
        """启用 Planner 时，年假申请经 leave_proposal_tool 走受控 Proposal 链路。

        Composite Enterprise Task P0：Planner 决策调用 leave_proposal_tool，
        Executor 执行后把 action_proposal 写回 State，随后 Planner finish。
        """
        proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
            action_type='ANNUAL_LEAVE_REQUEST',
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 20),
            reason='私事',
            half_day='NONE',
        ))
        decisions = [
            '{"action":"tool","tool_name":"leave_proposal_tool",'
            '"arguments":{},"reason_code":"need_proposal"}',
            '{"action":"finish","answer":"已生成年假申请草稿，请确认后提交。",'
            '"reason_code":"task_complete"}',
        ]
        proposal_payload = json.dumps({
            'kind': 'proposal',
            'action_proposal': proposal.proposal.model_dump(mode='json'),
            'missing_fields': [],
            'message': '已生成年假申请草稿，请确认后提交。',
        }, ensure_ascii=False)
        proposal_tool_mock = Mock()
        proposal_tool_mock.invoke.return_value = proposal_payload
        with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
             patch('app.agents.tool_executor_node.leave_proposal_tool',
                   proposal_tool_mock), \
             patch('app.services.tool_calling_service.plan_annual_leave_action') as planner:
            result = run_langgraph_agent(
                '申请2026-07-20一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                employee_id='E1001',
                use_planner=True,
            )
        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal']['action_type'] == 'ANNUAL_LEAVE_REQUEST'
        assert result['action_proposal']['start_date'] == date(2026, 7, 20)
        assert result['missing_fields'] == []
        planner.assert_not_called()  # Tool 已 stub，受控链路未直接触发
        serialized = str(result['action_proposal'])
        for forbidden in ('actionId', 'nonce', 'employeeId'):
            assert forbidden not in serialized
