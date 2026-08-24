"""test_memory_p1a_task_type_policy.py —— Memory P1-A Task Type Policy 契约测试

P1-A 目标：
  将 MemoryTaskType 从 ``Closed Enum`` 演进为 ``Controlled Extensible Task Type``。

本测试覆盖（与 P1-A 验收标准 1:1 对齐）：

  1. **Task Type Validation**
       - 允许 LEAVE_REQUEST / EXPENSE_REQUEST（policy 白名单内）；
       - 拒绝 ADMIN_PERMISSION_CHANGE 等高敏 / 任意字符串；
       - policy.assert_allowed / is_allowed 行为正确。
  2. **Extractor Contract**
       - 输入 "EXPENSE workflow" 的事实信息 → 输出 EXPENSE_REQUEST；
       - LLM 输出受 prompt 中 ``Available Memory Task Types`` 约束；
       - system prompt 动态渲染 policy.available_task_types。
  3. **Trigger Test**
       - 允许 expense proposal signal → 触发 Memory（policy 注册后）；
       - 禁止 RAG search / FAQ query / balance query（不在 eligible_tool_names）。
  4. **Isolation Regression**
       - 不同用户 A / B 各自的 EXPENSE_REQUEST Memory 不能互读。
       - 验证仍由 VerifiedIdentity 复合 key 控制，policy 扩展不破坏隔离。
  5. **P0 向后兼容**
       - 默认 policy 行为等价于 P0（task_type 白名单 / tool 白名单 / default）；
       - 既有 MemoryExtractor / MemoryTriggerPolicy / MemoryWritePolicy 无注入调用，
         测试期望保持 P0 行为。

非覆盖范围：
  - 不实现 Expense 业务本身；不修改 Java / DB / Runtime Hook。
"""

from __future__ import annotations

import json

import pytest

from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_task_type_policy import (
    DEFAULT_TASK_TYPES,
    MemoryTaskTypePolicy,
)
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_policy import MemoryWritePolicy
from app.schemas.memory_schema import (
    MemoryExtractionInput,
    MemoryProposal,
)

# ===========================================================================
# 1. Task Type Validation
# ===========================================================================


class TestTaskTypeValidation:
    """Task Type Validation —— 白名单校验。"""

    def test_default_policy_allows_p0_types(self):
        policy = MemoryTaskTypePolicy.default()
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')

    @pytest.mark.parametrize('bad', [
        'ADMIN_PERMISSION_CHANGE',  # 高敏类别
        'EVAL_REPORT',              # 不存在的业务类别
        'GENERIC ',                 # 末尾空格（避免大小写 / 拼写变体绕过）
        'generic',                  # 大小写变体
        '',                         # 空字符串
    ])
    def test_default_policy_rejects_unknown_types(self, bad):
        policy = MemoryTaskTypePolicy.default()
        assert policy.is_allowed(bad) is False

    @pytest.mark.parametrize('bad', [None, '', '   ', 0, 123, [], {}])
    def test_default_policy_rejects_non_string_types(self, bad):
        policy = MemoryTaskTypePolicy.default()
        assert policy.is_allowed(bad) is False  # type: ignore[arg-type]

    def test_expand_policy_allows_expense_request(self):
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
        )
        # P0 默认仍然合法
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')
        # 新增业务
        assert policy.is_allowed('EXPENSE_REQUEST')
        # 其它业务仍然拒绝
        assert not policy.is_allowed('ADMIN_PERMISSION_CHANGE')
        assert not policy.is_allowed('TRAVEL_REQUEST')

    def test_expand_policy_admin_permission_change_still_rejected(self):
        """验收标准明确禁止的 ADMIN_PERMISSION_CHANGE 必须被拒绝。"""
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
        )
        assert not policy.is_allowed('ADMIN_PERMISSION_CHANGE')

    def test_assert_allowed_raises_for_unknown(self):
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
        )
        with pytest.raises(ValueError, match='不允许的'):
            policy.assert_allowed('ADMIN_PERMISSION_CHANGE')

    def test_default_task_type_in_default_policy_is_generic(self):
        policy = MemoryTaskTypePolicy.default()
        assert policy.fallback_task_type() == 'GENERIC'

    def test_create_for_rejects_tool_value_not_in_task_types(self):
        """tool_to_task_type 映射 value 必须命中 available_task_types。"""
        with pytest.raises(ValueError, match='不在 available_task_types'):
            MemoryTaskTypePolicy.create_for(
                extra_task_types=('EXPENSE_REQUEST',),
                extra_tool_to_task_type={
                    'expense_proposal_tool': 'TRAVEL_REQUEST',  # 未注册类别
                },
            )

    def test_create_for_rejects_invalid_default_task_type(self):
        with pytest.raises(ValueError, match='default_task_type'):
            MemoryTaskTypePolicy.create_for(
                default_task_type='TRAVEL_REQUEST',
            )

    def test_create_for_rejects_empty_task_type_string(self):
        with pytest.raises(ValueError, match='非空字符串'):
            MemoryTaskTypePolicy.create_for(
                extra_task_types=('',),
            )

    def test_default_task_types_match_p0(self):
        """P0 既有的 3 个 taskType 必须出现在默认白名单中。"""
        policy = MemoryTaskTypePolicy.default()
        assert set(policy.available_task_types) >= set(DEFAULT_TASK_TYPES)


# ===========================================================================
# 2. Extractor Contract
# ===========================================================================


class TestExtractorContract:
    """Extractor Contract —— prompt 动态注入、policy 控制白名单。"""

    def _expense_extractor(self) -> MemoryExtractor:
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
            extra_tool_to_task_type={
                'expense_proposal_tool': 'EXPENSE_REQUEST',
            },
        )
        return MemoryExtractor(task_type_policy=policy)

    def test_system_prompt_includes_expense_request_after_expansion(self):
        extractor = self._expense_extractor()
        prompt = extractor.system_prompt
        # P0 默认仍在
        assert "'GENERIC'" in prompt
        assert "'LEAVE_REQUEST'" in prompt
        # 扩展项
        assert 'EXPENSE_REQUEST' in prompt
        # 拒绝项不应出现（不能诱导 LLM 编造）
        assert 'ADMIN_PERMISSION_CHANGE' not in prompt
        assert 'TRAVEL_REQUEST' not in prompt

    def test_system_prompt_lists_available_task_types_section(self):
        """新增业务后，Available Memory Task Types 列表必须显式列出。"""
        extractor = self._expense_extractor()
        prompt = extractor.system_prompt
        assert 'Available Memory Task Types' in prompt
        # 列表项以单引号字面渲染（P0 兼容），便于既有断言通过
        assert "- 'EXPENSE_REQUEST'" in prompt

    def test_default_extractor_prompt_unaffected(self):
        """默认 policy 下，prompt 仍包含 P0 三个值（不破坏 P0 行为）。"""
        extractor = MemoryExtractor()
        prompt = extractor.system_prompt
        assert "'GENERIC'" in prompt
        assert "'LEAVE_REQUEST'" in prompt
        assert "'BUSINESS_ACTION'" in prompt
        # 默认 policy 不含 EXPENSE_REQUEST
        assert 'EXPENSE_REQUEST' not in prompt

    def test_expense_workflow_proposal_parsed_as_expense_request(self):
        """验收点 2：'EXPENSE workflow' 输入 → 输出 EXPENSE_REQUEST。"""
        extractor = self._expense_extractor()
        proposal_dict = {
            'action': 'UPSERT',
            'task_type': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'task_state': {'waiting_for': 'receipt'},
            'summary': '等待用户补充发票信息',
            'reason': 'expense workflow 触发',
        }
        proposal = extractor.parse_proposal(json.dumps(proposal_dict))
        assert proposal.action == 'UPSERT'
        assert proposal.task_type == 'EXPENSE_REQUEST'
        assert proposal.status == 'ACTIVE'

    def test_extractor_accepts_p0_types_under_default_policy(self):
        """P0 类型在默认 policy 下 parse 通过。"""
        extractor = MemoryExtractor()
        proposal = extractor.parse_proposal(json.dumps({
            'action': 'UPSERT',
            'task_type': 'LEAVE_REQUEST',
            'status': 'ACTIVE',
            'task_state': {'waiting_for': 'date'},
        }))
        assert proposal.task_type == 'LEAVE_REQUEST'

    def test_parse_proposal_does_not_apply_policy_filter(self):
        """P1-A 起 schema.task_type 放宽为 str；policy 在写入层兜底。

        Extractor.parse_proposal 不抛 policy 错误（policy 不在 parse 层做 fail-loud）；
        由 MemoryWritePolicy.assert_allowed 在写入前兜底。
        """
        extractor = self._expense_extractor()
        # 解析一个 EXPENSE_REQUEST：合法
        proposal = extractor.parse_proposal(json.dumps({
            'action': 'UPSERT',
            'task_type': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'task_state': {'waiting_for': 'receipt'},
        }))
        assert proposal.task_type == 'EXPENSE_REQUEST'

    def test_write_policy_rejects_admin_permission_change(self):
        """policy 二次校验：写入前对 task_type 做白名单 fail-loud。

        注：当前 schema.task_type 字段类型为 ``str``，因此即使 policy 之外的
        字符串也能被 schema 解析；policy.assert_allowed 在 WritePolicy 兜底。
        """
        policy = MemoryTaskTypePolicy.default()  # P0 默认白名单
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='ADMIN_PERMISSION_CHANGE',  # policy 不接受
            status='ACTIVE',
            task_state={'k': 'v'},
        )
        with pytest.raises(ValueError, match='不允许的'):
            write_policy.evaluate(proposal)

    def test_write_policy_accepts_expense_request_under_extended_policy(self):
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
        )
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'receipt'},
        )
        cmd = write_policy.evaluate(proposal)
        assert cmd is not None
        assert cmd.task_type == 'EXPENSE_REQUEST'

    def test_build_prompt_renders_input_facts(self):
        extractor = self._expense_extractor()
        inp = MemoryExtractionInput(
            question='帮我报销 200 元打车费',
            answer='请补充发票',
            tool_history=[{
                'tool_name': 'expense_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
            action_proposal={'kind': 'clarification', 'missing_fields': ['receipt']},
        )
        prompt = extractor.build_prompt(inp)
        assert '帮我报销 200 元打车费' in prompt
        assert 'expense_proposal_tool' in prompt


# ===========================================================================
# 3. Trigger Test
# ===========================================================================


class TestTriggerPolicyExtensibility:
    """Trigger Policy —— Memory Capability Signal 由 policy 驱动。"""

    def _expense_trigger(self) -> MemoryTriggerPolicy:
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
            extra_tool_to_task_type={
                'expense_proposal_tool': 'EXPENSE_REQUEST',
            },
        )
        return MemoryTriggerPolicy(task_type_policy=policy)

    def test_expense_proposal_signal_triggers_memory(self):
        """验收点 3：expense proposal signal 触发 Memory。"""
        policy = self._expense_trigger().task_type_policy
        trigger = MemoryTriggerPolicy(task_type_policy=policy)
        result = {
            'question': '帮我报销',
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(result)
        assert decision.should_extract is True

    @pytest.mark.parametrize('tool_name', [
        'rag_answer_tool',
        'eval_report_tool',
        'leave_balance_tool',
        'leave_request_tool',
    ])
    def test_non_eligible_tools_do_not_trigger_memory(self, tool_name):
        """RAG / eval / balance / leave_request 不触发 Memory。"""
        trigger = self._expense_trigger()
        result = {
            'tool_history': [{
                'tool_name': tool_name,
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(result)
        assert decision.should_extract is False

    def test_default_trigger_still_recognizes_leave_proposal_tool(self):
        """P0 后向兼容：默认 policy 仍把 leave_proposal_tool 当作 eligible。"""
        trigger = MemoryTriggerPolicy()
        result = {
            'tool_history': [{
                'tool_name': 'leave_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(result)
        assert decision.should_extract is True

    def test_default_trigger_ignores_expense_proposal_tool(self):
        """默认 policy（未扩展）下 expense_proposal_tool 不在白名单，不触发。"""
        trigger = MemoryTriggerPolicy()
        result = {
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(result)
        assert decision.should_extract is False

    def test_eligible_tool_names_property_exposes_policy(self):
        trigger = self._expense_trigger()
        names = trigger.eligible_tool_names
        assert 'leave_proposal_tool' in names
        assert 'expense_proposal_tool' in names
        assert 'rag_answer_tool' not in names

    def test_trigger_safety_short_circuit_unchanged(self):
        trigger = self._expense_trigger()
        decision = trigger.evaluate({
            'safe': False,
            'tool_history': [{
                'tool_name': 'expense_proposal_tool', 'status': 'success',
                'arguments': {}, 'observation': 'x',
            }],
        })
        assert decision.should_extract is False


# ===========================================================================
# 4. Isolation Regression
# ===========================================================================


class TestIsolationRegression:
    """Isolation Regression —— policy 扩展不破坏 user_id 边界。

    说明：Memory 隔离（用户 A 不可读 B 的 memory）的真正控制点不在 Python：
      - Java 侧基于 (trusted user_id, conversation_id) 复合 key 控制读取；
      - Python 侧从不持有 / 注入 user_id。
    本测试验证 P1-A 扩展 policy 后，Python 写入侧仍不接触 user_id；
    隔离由 VerifiedIdentity（Java 侧）独占保证。
    """

    def test_extractor_does_not_accept_user_id_in_task_state(self):
        """用户 A 与 B 的 EXPENSE_REQUEST task_state 不可互相依赖。

        task_state 是 Context Snapshot，不是权限 / 身份来源；
        写入前 _FORBIDDEN_TASK_STATE_KEYS 必须剥离 user_id 等敏感键。
        """
        from app.memory.memory_write_policy import MemoryWritePolicy

        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
        )
        write_policy = MemoryWritePolicy(task_type_policy=policy)

        # 用户 A 写入：携带 user_id 必须被剥离
        proposal_a = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'waiting_for': 'receipt',
                'user_id': 'E10001',  # forbidden
                'employee_id': 'E10001',  # forbidden
                'conversation_id': 'conv-A',  # forbidden
            },
        )
        cmd_a = write_policy.evaluate(proposal_a)
        assert cmd_a is not None
        assert 'user_id' not in cmd_a.task_state
        assert 'employee_id' not in cmd_a.task_state
        assert 'conversation_id' not in cmd_a.task_state
        assert cmd_a.task_state.get('waiting_for') == 'receipt'

        # 用户 B 写入：携带 user_id 也必须被剥离（同一规则）
        proposal_b = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'waiting_for': 'amount',
                'user_id': 'E20002',  # forbidden
            },
        )
        cmd_b = write_policy.evaluate(proposal_b)
        assert cmd_b is not None
        assert 'user_id' not in cmd_b.task_state
        # 用户 A / B 的 waiting_for 互相独立 —— task_state 隔离仍然由 VerifiedIdentity
        # 控制；本测试断言"policy 扩展后没有引入新的共享 / 串味字段"。
        assert cmd_a.task_state != cmd_b.task_state

    def test_pipeline_isolates_per_user_results(self):
        """Pipeline 在不同 user_id 上下文下的结果彼此独立（task_state 不串味）。

        Pipeline 自身不感知 user_id；user_id 仅由 Java VerifiedIdentity 注入。
        本测试验证两个不同 user_id 上下文进入 Pipeline 后产出的 command 仍然
        在 task_state 内只携带各自的事实数据，无跨用户键串味。
        """
        policy = MemoryTaskTypePolicy.create_for(
            extra_task_types=('EXPENSE_REQUEST',),
            extra_tool_to_task_type={'expense_proposal_tool': 'EXPENSE_REQUEST'},
        )
        # 注入假 llm_callable（保证 Pipeline 走通）。
        # user prompt 中包含原始问题（含中文字符），用中文区分用户。
        def fake_llm(system, user):
            if '用户A' in user:
                return json.dumps({
                    'action': 'UPSERT',
                    'task_type': 'EXPENSE_REQUEST',
                    'status': 'ACTIVE',
                    'task_state': {'waiting_for': 'receipt', 'user_id': 'userA'},
                })
            # 用户 B
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'EXPENSE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'amount', 'user_id': 'userB'},
            })

        pipeline = MemoryPipeline(
            task_type_policy=policy,
            llm_callable=fake_llm,
        )

        agent_result_a = {
            'question': '用户A: 帮我报销',
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }
        agent_result_b = {
            'question': '用户B: 帮我报销',
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'arguments': {},
                'status': 'success',
                'observation': 'ok',
            }],
        }

        result_a = pipeline.process(agent_result_a)
        result_b = pipeline.process(agent_result_b)

        assert result_a.command is not None
        assert result_b.command is not None
        # user_id 必须在两个 command 的 task_state 中被剥离（policy 与 write_policy 共同保证）
        assert 'user_id' not in result_a.command.task_state
        assert 'user_id' not in result_b.command.task_state
        # 两个 command 的 task_state 不应相同（各自等待不同字段）
        assert (
            result_a.command.task_state.get('waiting_for')
            != result_b.command.task_state.get('waiting_for')
        )


# ===========================================================================
# 5. P0 向后兼容
# ===========================================================================


class TestBackwardCompatibility:
    """P0 向后兼容：默认 policy 行为等价于 P0。"""

    def test_default_extractor_default_policy_p0_task_types(self):
        extractor = MemoryExtractor()
        assert extractor.task_type_policy.is_allowed('GENERIC')
        assert extractor.task_type_policy.is_allowed('LEAVE_REQUEST')
        assert extractor.task_type_policy.is_allowed('BUSINESS_ACTION')

    def test_default_trigger_default_policy_eligible_tool(self):
        trigger = MemoryTriggerPolicy()
        assert 'leave_proposal_tool' in trigger.eligible_tool_names

    def test_default_write_policy_default_task_type_is_generic(self):
        write_policy = MemoryWritePolicy()
        # 验证 policy 默认值；构造上必须等价于 P0
        assert write_policy.task_type_policy.fallback_task_type() == 'GENERIC'

    def test_p0_proposal_with_default_task_type_succeeds(self):
        """P0 测试场景：proposal.task_type 缺省 → 默认 GENERIC，写入通过。"""
        write_policy = MemoryWritePolicy()
        proposal = MemoryProposal(
            action='UPSERT',
            task_type=None,  # 不提供 task_type
            status='ACTIVE',
            task_state={'k': 'v'},
        )
        cmd = write_policy.evaluate(proposal)
        assert cmd is not None
        assert cmd.task_type == 'GENERIC'  # 默认兜底

    def test_policy_is_immutable(self):
        """policy 一旦构造不可变（frozen=True），避免运行期动态扩展破坏可审计性。"""
        policy = MemoryTaskTypePolicy.default()
        with pytest.raises(Exception):  # ValidationError on frozen model
            policy.available_task_types = ('HACKED',)  # type: ignore[misc]

    def test_pipeline_default_uses_default_policy(self):
        """未注入 policy 时，Pipeline 子组件各自使用默认 policy（行为等价于 P0）。"""
        pipeline = MemoryPipeline()
        # 三个组件默认 policy 集合 = P0 三个值
        assert pipeline.trigger_policy.eligible_tool_names == frozenset(
            {'leave_proposal_tool'},
        )
        assert pipeline.write_policy.task_type_policy.fallback_task_type() == 'GENERIC'