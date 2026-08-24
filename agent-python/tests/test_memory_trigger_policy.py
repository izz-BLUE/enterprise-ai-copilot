"""test_memory_trigger_policy.py —— Memory Trigger Policy 测试

覆盖：

正常 / 触发：
  1. action_proposal 存在 → 触发（reason=action_proposal_present）
  2. tool_history 含至少一条 success Tool → 触发（reason=tool_history_has_success）
  3. existing_memory 非空 → 触发（reason=existing_memory_present）
  4. 多重信号同时存在 → 触发（按优先级 action_proposal > tool_success > existing_memory）
  5. action_proposal 含 Clarification kind → 仍然触发

不触发：
  6. 完全空执行（question 空 + 无 tool + 无 action_proposal + 无 existing_memory）
  7. tool_history 全是 blocked / error → 不触发
  8. existing_memory 空 dict → 不触发

Safety 短路：
  9. safe=False → 直接不触发（reason=safety_blocked）

Agent 失败终态短路：
 10. route=error / stop_reason 失败集合（provider_error / invalid_decision /
     step_budget_exhausted）→ 不触发（reason=agent_failure_terminal），
     即使已有 ACTIVE memory / action_proposal / tool success

边界 / 契约：
 11. 非 dict 输入抛 TypeError
 12. MemoryTriggerDecision extra='forbid'
 13. MemoryTriggerDecision should_extract 是 bool
 14. 评估是 pure-function（多次调用结果一致，无副作用）
"""

import pytest
from pydantic import ValidationError

from app.memory.memory_trigger_policy import (
    NO_TRIGGER_REASON_AGENT_FAILURE,
    NO_TRIGGER_REASON_NO_SIGNAL,
    NO_TRIGGER_REASON_SAFETY_BLOCKED,
    TRIGGER_REASON_ACTION_PROPOSAL,
    TRIGGER_REASON_EXISTING_MEMORY,
    TRIGGER_REASON_TOOL_SUCCESS,
    MemoryTriggerDecision,
    MemoryTriggerPolicy,
)


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

    def test_existing_memory_present_triggers(self, policy):
        result = {
            'question': '继续上次的任务',
            'memory_context': {
                'taskType': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'taskStateJson': '{"waiting_for": "date"}',
                'summary': '等待用户补充请假日期',
            },
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_EXISTING_MEMORY

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

    def test_priority_tool_success_wins_over_existing_memory(self, policy):
        """业务 tool_history 优先级高于 existing_memory。"""
        result = {
            'tool_history': [{'tool_name': 'leave_proposal_tool', 'status': 'success',
                              'arguments': {}, 'observation': 'x'}],
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.reason == TRIGGER_REASON_TOOL_SUCCESS


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

    def test_existing_memory_empty_dict_no_trigger(self, policy):
        """空 dict 视为 None 等价：不触发。"""
        decision = policy.evaluate({'memory_context': {}})
        assert decision.should_extract is False
        assert decision.reason == NO_TRIGGER_REASON_NO_SIGNAL

    def test_existing_memory_null_no_trigger(self, policy):
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
    def test_route_error_with_existing_memory_blocks_trigger(self, policy):
        """审计反例：已有 ACTIVE memory + route=error，不得触发 Extractor。"""
        result = {
            'question': '继续上次的任务',
            'route': 'error',
            'stop_reason': 'provider_error',
            'answer': '服务暂时不可用，请稍后重试。',
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

    def test_normal_termination_still_triggers(self, policy):
        """正常终态（task_complete）不受影响：existing_memory 仍然触发。"""
        result = {
            'route': 'agent',
            'stop_reason': 'task_complete',
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
        }
        decision = policy.evaluate(result)
        assert decision.should_extract is True
        assert decision.reason == TRIGGER_REASON_EXISTING_MEMORY

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
