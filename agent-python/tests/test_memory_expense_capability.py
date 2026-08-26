"""test_memory_expense_capability.py —— P2-A EXPENSE_REQUEST Memory 接入测试

V2 §二十六 / §二十七：
- EXPENSE_REQUEST 只通过 Capability Registry 注册（DEFAULT_P0_CAPABILITIES）
- 没有业务双重 hard-code（DEFAULT_TOOL_TO_TASK_TYPE 未加 expense）
- 其它 Expense read tools（travel_record / invoice_verify / expense_status /
  rag_answer）不触发 Extractor
- Memory Core（trigger / write / extractor）未硬编码 Expense
"""

from __future__ import annotations

from app.capabilities.expense_capability import EXPENSE_MEMORY_CAPABILITY
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.capabilities.p0_default_capabilities import DEFAULT_P0_CAPABILITIES
from app.memory.memory_task_type_policy import (
    DEFAULT_TOOL_TO_TASK_TYPE,
    MemoryTaskTypePolicy,
)
from app.memory.memory_trigger_policy import MemoryTriggerPolicy


def _registry_policy() -> MemoryTaskTypePolicy:
    return MemoryTaskTypePolicy.create_from_registry(
        MemoryCapabilityRegistry.of(DEFAULT_P0_CAPABILITIES))


class TestExpenseCapabilityRegistration:
    def test_registry_contains_expense_request(self):
        registry = MemoryCapabilityRegistry.of(DEFAULT_P0_CAPABILITIES)
        assert 'EXPENSE_REQUEST' in registry.task_types()
        assert registry.tool_mapping().get('expense_proposal_tool') == 'EXPENSE_REQUEST'

    def test_expense_capability_eligible_tool(self):
        assert EXPENSE_MEMORY_CAPABILITY.task_type == 'EXPENSE_REQUEST'
        assert EXPENSE_MEMORY_CAPABILITY.eligible_tools == frozenset(
            {'expense_proposal_tool'})

    def test_no_double_registration_in_legacy_default_map(self):
        """V2 §二十六：禁止双重 hardcode —— DEFAULT_TOOL_TO_TASK_TYPE 不含 expense。"""
        assert 'expense_proposal_tool' not in DEFAULT_TOOL_TO_TASK_TYPE
        # 与 create_from_registry 的官方入口一致：expense 只经 registry 进入。
        policy = _registry_policy()
        assert policy.tool_to_task_type['expense_proposal_tool'] == 'EXPENSE_REQUEST'

    def test_registry_eligible_tools_only_proposal(self):
        registry = MemoryCapabilityRegistry.of(DEFAULT_P0_CAPABILITIES)
        assert registry.tool_mapping().get('travel_record_tool') is None
        assert registry.tool_mapping().get('invoice_verify_tool') is None
        assert registry.tool_mapping().get('expense_status_tool') is None
        assert registry.tool_mapping().get('rag_answer_tool') is None


class TestExpenseMemoryTrigger:
    def _evaluate(self, tool_history, action_proposal=None):
        trigger = MemoryTriggerPolicy(task_type_policy=_registry_policy())
        # MemoryTriggerPolicy.evaluate 接收 agent_result dict（V2 §二十七 契约）
        return trigger.evaluate({
            'safe': True,
            'route': 'action',
            'stop_reason': 'task_complete',
            'tool_history': tool_history,
            'action_proposal': action_proposal,
        })

    def test_expense_proposal_success_triggers(self):
        """Stress E：expense_proposal_tool success → Memory Trigger 触发。"""
        decision = self._evaluate([{
            'tool_name': 'expense_proposal_tool',
            'arguments': {},
            'status': 'success',
            'observation': '{"success": true}',
        }])
        assert decision.should_extract is True

    def test_expense_read_tools_do_not_trigger(self):
        """V2 §十六：travel / invoice / rag 单独成功不触发 Memory Extractor。"""
        for tool_name in ('travel_record_tool', 'invoice_verify_tool',
                          'expense_status_tool', 'rag_answer_tool'):
            decision = self._evaluate([{
                'tool_name': tool_name,
                'arguments': {},
                'status': 'success',
                'observation': '{"success": true}',
            }])
            assert decision.should_extract is False, tool_name

    def test_action_proposal_present_triggers(self):
        decision = self._evaluate([], action_proposal={'action_type': 'EXPENSE_CLAIM'})
        assert decision.should_extract is True


class TestMemoryCoreNotHardcoded:
    def test_trigger_policy_uses_policy_not_literal(self):
        """Memory Core 通过 policy 消费；未硬编码 expense tool 白名单。"""
        policy = _registry_policy()
        trigger = MemoryTriggerPolicy(task_type_policy=policy)
        assert trigger.task_type_policy.eligible_tool_names() == frozenset(
            {'leave_proposal_tool', 'expense_proposal_tool'})
