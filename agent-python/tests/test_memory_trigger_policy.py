"""test_memory_trigger_policy.py —— Memory Trigger Policy 测试

Trigger 语义收敛后覆盖：

正常 / 触发：
  1. action_proposal 存在 → 触发（reason=action_proposal_present）
  2. tool_history 含至少一条 success Memory-eligible Tool → 触发
     （reason=tool_history_has_success）
  3. action_proposal 含 Clarification kind → 仍然触发
  4. 多重信号同时存在 → 触发（优先级 action_proposal > tool_success；
     Read Path 注入的 memory_context 不参与触发优先级）

不触发（Case A / Case B）：
  5. 完全空执行：question 空 + 无 tool + 无 action_proposal
  6. tool_history 全是 blocked / error → 不触发
  7. ACTIVE Memory + RAG（Case A）：memory_context 仅做 Read Path 上下文，
     不再当作 Trigger 信号 → 不触发
  8. ACTIVE Memory + 无关查询（Case B）：同上 → 不触发

Safety 短路（Case E）：
  9. safe=False → 直接不触发（reason=safety_blocked）

Agent 失败终态短路（Case F）：
 10. route=error / stop_reason 失败集合（provider_error / invalid_decision /
     step_budget_exhausted）→ 不触发（reason=agent_failure_terminal），
     即使已有 action_proposal / tool success

边界 / 契约：
 11. 非 dict 输入抛 TypeError
 12. MemoryTriggerDecision extra='forbid'
 13. MemoryTriggerDecision should_extract 是 bool
 14. 评估是 pure-function（多次调用结果一致，无副作用）
 15. Default Task Type Policy 透传：leave_proposal_tool 仍为 eligible

扩展入口红线：business_state_signal 等尚未接线的信号源当前不在 Trigger 关心
范围内，触发必须由现有 Task Type Policy / Capability Registry 提供的白名单
决定，不接受任何新增"半成品 signal"。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.memory_trigger_policy import (
    NO_TRIGGER_REASON_AGENT_FAILURE,
    NO_TRIGGER_REASON_NO_SIGNAL,
    NO_TRIGGER_REASON_SAFETY_BLOCKED,
    TRIGGER_REASON_ACTION_PROPOSAL,
    TRIGGER_REASON_TOOL_SUCCESS,
    MemoryTriggerDecision,
    MemoryTriggerPolicy,
)
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy


@pytest.fixture
def policy() -> MemoryTriggerPolicy:
    return MemoryTriggerPolicy()


# ---------- 触发 ----------

class TestTriggerFire:
    def test_action_proposal_present(self, policy):
        result = {
            'question': '请帮我请假',
            'answer': '...',
            'action_proposal': {
                'action_type': 'ANNUAL_LEAVE_REQUEST',
                'start_date': '2026-08-25',
                'end_date': '2026-08-25',
            },
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_ACTION_PROPOSAL

    def test_action_proposal_clarification_triggers(self, policy):
        """Clarification（业务动作链路缺字段）也触发：跨请求需要续补。"""
        result = {
            'question': '我想请假',
            'answer': '请补充日期',
            'action_proposal': {'kind': 'clarification', 'missing_fields': ['start_date']},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_ACTION_PROPOSAL

    def test_leave_proposal_success_triggers(self, policy):
        """Memory-eligible Tool（leave_proposal_tool）成功触发。"""
        result = {
            'question': '年假制度是什么',
            'answer': '入职满1年5天',
            'tool_history': [
                {
                    'tool_name': 'leave_proposal_tool',
                    'arguments': {'question': '年假制度'},
                    'status': 'success',
                    'observation': 'ok',
                },
            ],
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_TOOL_SUCCESS

    def test_multiple_successful_tool_calls_triggers_for_business_tool(self, policy):
        """普通查询成功不触发，业务 Proposal 成功仍只触发一次。"""
        result = {
            'tool_history': [
                {'tool_name': 'rag_answer_tool', 'status': 'success', 'arguments': {}, 'observation': 'r'},
                {'tool_name': 'leave_proposal_tool', 'status': 'success', 'arguments': {}, 'observation': 'p'},
            ],
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_TOOL_SUCCESS

    def test_priority_action_proposal_wins_over_tool_success(self, policy):
        """action_proposal 优先级高于 tool_history。"""
        result = {
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.reason == TRIGGER_REASON_ACTION_PROPOSAL


# ---------- 不触发 ----------

class TestNoTrigger:
    def test_empty_execution_no_trigger(self, policy):
        result = {
            'question': '...',
            'answer': '...',
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_completely_empty_dict_no_trigger(self, policy):
        decision = policy.evaluate({})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_tool_history_only_blocked_no_trigger(self, policy):
        result = {
            'tool_history': [
                {'tool_name': 'rag_answer_tool', 'status': 'blocked', 'arguments': {}, 'observation': ''},
                {'tool_name': 'leave_balance_tool', 'status': 'error', 'arguments': {}, 'observation': 'failed'},
            ],
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_empty_tool_history_no_trigger(self, policy):
        decision = policy.evaluate({'tool_history': []})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    # ---- Read Path 注入不再触发（Case A / Case B） ----

    def test_active_memory_with_rag_does_not_trigger(self, policy):
        """Case A：ACTIVE Memory + RAG（Memory-eligible 之外 Tool 成功）→ 不触发。

        历史 ACTIVE Memory 只是 Planner 的不可信上下文，不再是 Trigger 的触发信号。
        """
        result = {
            'question': '公司的春节假期安排是什么？',
            'answer': '...',
            'tool_history': [
                {'tool_name': 'rag_answer_tool', 'status': 'success',
                 'arguments': {}, 'observation': 'ok'},
            ],
            'memory_context': {
                'taskType': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'taskStateJson': '{"waiting_for": "date"}',
                'summary': '等待用户补充请假日期',
            },
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_active_memory_with_unrelated_query_does_not_trigger(self, policy):
        """Case B：ACTIVE Memory + 无关查询（无任何业务信号）→ 不触发。"""
        result = {
            'question': '顺便问一下，公司食堂几点开？',
            'answer': '...',
            'memory_context': {
                'taskType': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'taskStateJson': '{"waiting_for": "date"}',
                'summary': '等待用户补充请假日期',
            },
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_active_memory_empty_dict_does_not_trigger(self, policy):
        """memory_context 空 dict 视为 None 等价：不触发。"""
        decision = policy.evaluate({'memory_context': {}})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_active_memory_null_does_not_trigger(self, policy):
        decision = policy.evaluate({'memory_context': None})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_action_proposal_null_no_trigger(self, policy):
        decision = policy.evaluate({'action_proposal': None})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_rag_success_does_not_trigger(self, policy):
        decision = policy.evaluate({
            'tool_history': [{'tool_name': 'rag_answer_tool', 'status': 'success'}],
        })
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_leave_balance_success_does_not_trigger(self, policy):
        decision = policy.evaluate({
            'tool_history': [{'tool_name': 'leave_balance_tool', 'status': 'success'}],
        })
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL


# ---------- Safety 短路 ----------

class TestSafetyShortCircuit:
    def test_safe_false_blocks_trigger(self, policy):
        """Case E：safe=False + 同时存在 action_proposal / eligible tool → safety_blocked。"""
        result = {
            'safe': False,
            'reason': 'prompt_override',
            'category': 'safety',
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
            'action_proposal': {'action_type': 'X'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_SAFETY_BLOCKED


# ---------- Agent 失败终态短路 ----------

class TestAgentFailureShortCircuit:
    def test_route_error_with_business_signals_blocks_trigger(self, policy):
        """Case F：失败终态（route=error）+ 多个正向信号并存 → agent_failure_terminal。"""
        result = {
            'question': '继续上次的任务',
            'route': 'error',
            'stop_reason': 'provider_error',
            'answer': '服务暂时不可用，请稍后重试。',
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
            'memory_context': {
                'taskType': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'taskStateJson': '{"waiting_for": "date"}',
                'summary': '等待用户补充请假日期',
            },
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_AGENT_FAILURE

    def test_provider_error_blocks_trigger_even_with_action_proposal(self, policy):
        result = {
            'route': 'error',
            'stop_reason': 'provider_error',
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_AGENT_FAILURE

    @pytest.mark.parametrize('stop_reason', [
        'provider_error',
        'invalid_decision',
        'step_budget_exhausted',
    ])
    def test_failure_stop_reasons_block_trigger(self, policy, stop_reason):
        result = {
            'route': 'error',
            'stop_reason': stop_reason,
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_AGENT_FAILURE

    def test_route_error_without_stop_reason_blocks_trigger(self, policy):
        result = {
            'route': 'error',
            'answer': '...',
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_AGENT_FAILURE

    def test_normal_termination_does_not_trigger_without_business_signals(self, policy):
        """正常终态（task_complete）+ memory_context 不再触发（Case A/B 已覆盖语义）。"""
        result = {
            'route': 'agent',
            'stop_reason': 'task_complete',
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_normal_termination_with_action_proposal_still_triggers(self, policy):
        """正常终态 + action_proposal：触发优先级不受 normal_termination 影响。"""
        result = {
            'route': 'agent',
            'stop_reason': 'task_complete',
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_ACTION_PROPOSAL

    def test_safety_blocked_takes_priority_over_failure(self, policy):
        """safe=False 与失败终态并存时，仍按 Safety 短路（保持原语义）。"""
        result = {
            'safe': False,
            'route': 'error',
            'stop_reason': 'provider_error',
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_SAFETY_BLOCKED


# ---------- 契约 / 边界 ----------

class TestPolicyContract:
    def test_non_dict_raises(self, policy):
        with pytest.raises(TypeError):
            policy.evaluate('not a dict')
        with pytest.raises(TypeError):
            policy.evaluate(None)
        with pytest.raises(TypeError):
            policy.evaluate([1, 2])

    def test_decision_extra_forbid(self):
        with pytest.raises(ValidationError):
            MemoryTriggerDecision(should_extract=True, reason='x', forged_field='y')

    def test_decision_should_extract_is_bool(self, policy):
        decision = policy.evaluate({})
        assert isinstance(decision.should_extract, bool)

    def test_pure_function_no_side_effects(self, policy):
        """同一输入多次调用应返回相同结果；policy 不修改输入 dict。"""
        result = {
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
        }
        snapshot = dict(result)
        snapshot['tool_history'] = list(snapshot['tool_history'])
        for _ in range(3):
            decision = policy.evaluate(result)
            assert decision.reason == TRIGGER_REASON_TOOL_SUCCESS
        # 输入 dict 不被修改
        assert result == snapshot

    def test_malformed_tool_history_entry_does_not_break(self, policy):
        """tool_history 含非 dict 项：跳过（不抛错）；触发规则按 dict 项判断。"""
        result = {
            'tool_history': [
                'unexpected string entry',
                None,
                 {'tool_name': 'leave_proposal_tool', 'status': 'success',
                 'arguments': {}, 'observation': 'x'},
            ],
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True


# ---------- 生产 Capability Registry 校验 ----------

class TestProductionCapabilityRegistryContract:
    """确认 Trigger 继续依赖现有 Task Type Policy / Capability Registry：

    - ``rag_answer_tool`` 不能是 Memory-eligible tool（普通 RAG 不触发）；
    - ``leave_proposal_tool`` 必须是 Memory-eligible tool（业务动作链路触发）。

    Trigger 层不维护第二份工具白名单；以下断言直接读取当前默认 policy
    的 ``eligible_tool_names``，与 ``memory_task_type_policy`` / 业务注册点
    保持一致。
    """

    def test_default_policy_does_not_include_rag_answer_tool(self, policy):
        assert 'rag_answer_tool' not in policy.eligible_tool_names

    def test_default_policy_includes_leave_proposal_tool(self, policy):
        assert 'leave_proposal_tool' in policy.eligible_tool_names

    def test_default_policy_matches_memory_task_type_policy(self):
        """Trigger 透传的 eligible_tool_names 必须与 Task Type Policy 完全一致。"""
        trigger_policy = MemoryTriggerPolicy()
        task_type_policy = MemoryTaskTypePolicy.default()
        assert trigger_policy.eligible_tool_names == task_type_policy.eligible_tool_names()
