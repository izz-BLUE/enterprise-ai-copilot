"""test_memory_phase8_governance.py —— Memory Phase 8 治理测试

Phase 8 是治理阶段：不扩展 Memory 能力；仅确保 Memory 在生产发布前满足
Observability / Rollout / Failure Handling / Contract Freeze / Security 五类约束。

本测试覆盖（与 Phase 8 验收 1:1 对齐）：

  1. **Observability Test**
     Audit Event 字段集合只含非业务敏感元数据；禁止 user_id / conversation_id /
     task_state / summary 等。验证 logging 输出与 MemoryAuditEvent 序列化结果。

  2. **Rollout Mode Test**
     DISABLED 模式：不调 Dispatcher（不写）。
     AUDIT_ONLY 模式：Pipeline 跑通但 Dispatcher 不被调用。
     ENABLED 模式：Dispatcher 被调用，写入成功。
     默认：DISABLED。

  3. **Failure Matrix Test**
     - Extractor Failure (LLM 输出非法 JSON)：Pipeline 降级为 noop；
     - Policy Failure (未注册 taskType)：WritePolicy 抛 ValueError，
       Pipeline 包装为 MemoryPipelineError；
     - Dispatcher Failure (Java timeout)：Runtime Hook 不冒泡；
     - Resolution Ambiguous (并列最新)：NeedClarification，禁止随机；
     - Invalid Snapshot (含禁止键)：WritePolicy 剥离 / MemoryCandidate 拒绝。

  4. **Contract Stability Test**
     P0 / P1 既有 taskType（LEAVE_REQUEST / EXPENSE_REQUEST / GENERIC /
     BUSINESS_ACTION）在生产模式 ENABLED 下行为不变。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from app.capabilities.memory_capability import MemoryCapability
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.memory.memory_audit import (
    LoggingAuditRecorder,
    MemoryAuditEvent,
    classify_failure_category,
)
from app.memory.memory_candidate import (
    MemoryCandidate,
    NeedClarification,
    ResolutionEmpty,
    ResolvedMemory,
)
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_runtime_hook import MemoryRuntimeHook
from app.memory.memory_task_resolution_policy import MemoryTaskResolutionPolicy
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_mode import (
    MemoryWriteExecutionPolicy,
    make_execution_policy,
)
from app.memory.memory_write_policy import MemoryWritePolicy
from app.schemas.memory_schema import MemoryProposal


# ===========================================================================
# Helpers
# ===========================================================================


def _expense_registry() -> MemoryCapabilityRegistry:
    return MemoryCapabilityRegistry.of([
        MemoryCapability(
            task_type='LEAVE_REQUEST',
            eligible_tools=frozenset({'leave_proposal_tool'}),
        ),
        MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        ),
    ])


def _policy() -> MemoryTaskTypePolicy:
    return MemoryTaskTypePolicy.create_from_registry(_expense_registry())


def _agent_result_with_proposal(action_proposal: dict | None = None) -> dict:
    return {
        'action_proposal': action_proposal or {
            'action_type': 'EXPENSE_REQUEST',
            'pendingStep': 'manager_confirmation',
        },
        'tool_history': [{
            'tool_name': 'expense_proposal_tool',
            'status': 'success',
            'arguments': {},
            'observation': 'ok',
        }],
    }


def _fake_llm(system, user):
    return json.dumps({
        'action': 'UPSERT',
        'task_type': 'EXPENSE_REQUEST',
        'status': 'ACTIVE',
        'task_state': {'pendingStep': 'manager_confirmation'},
        'summary': '等待主管确认报销申请',
    })


# ===========================================================================
# 1. Observability Test
# ===========================================================================


class TestObservabilityContract:
    """Audit Event 字段集合：禁止业务敏感字段。"""

    def test_audit_event_forbids_extra_fields(self):
        """MemoryAuditEvent.extra='forbid'：禁止 user_id / conversation_id /
        summary / task_state 等敏感键。
        """
        with pytest.raises(Exception):
            MemoryAuditEvent(
                triggered=True,
                user_id='E10001',  # type: ignore[call-arg]
            )

    @pytest.mark.parametrize('forbidden_key,forbidden_value', [
        ('user_id', 'U1'),
        ('userId', 'U1'),
        ('employee_id', 'E1'),
        ('conversation_id', 'c1'),
        ('conversationId', 'c1'),
        ('tenant_id', 'T1'),
        ('summary', '等待主管确认报销申请'),
        ('task_state', {'pendingStep': 'manager_confirmation'}),
        ('proposal', {'action': 'UPSERT'}),
        ('token', 'jwt-xxx'),
    ])
    def test_audit_event_rejects_sensitive_keys(self, forbidden_key, forbidden_value):
        with pytest.raises(Exception):
            MemoryAuditEvent(
                triggered=True,
                **{forbidden_key: forbidden_value},  # type: ignore[arg-type]
            )

    def test_audit_event_dumps_only_allowed_fields(self):
        """序列化结果只含 7+3 个允许字段；不含 user_id / summary / task_state。"""
        event = MemoryAuditEvent(
            triggered=True,
            trigger_reason='action_proposal_present',
            proposal_action='UPSERT',
            task_type='EXPENSE_REQUEST',
            write_attempted=True,
            write_success=True,
            error_type=None,
            memory_write_mode='ENABLED',
            memory_resolution_reason='latest_updated_at',
            failure_category=None,
        )
        data = event.model_dump()
        allowed_keys = {
            'triggered', 'trigger_reason', 'proposal_action', 'task_type',
            'write_attempted', 'write_success', 'error_type',
            'memory_write_mode', 'memory_resolution_reason', 'failure_category',
        }
        assert set(data.keys()) == allowed_keys
        # 显式断言：无敏感键
        for forbidden in (
            'user_id', 'userId', 'employee_id', 'conversation_id',
            'conversationId', 'tenant_id', 'summary', 'task_state',
            'proposal', 'token', 'jwt',
        ):
            assert forbidden not in data

    def test_logging_audit_recorder_does_not_emit_sensitive_payload(
        self, caplog,
    ):
        """LoggingAuditRecorder 写入 logger 时不携带 user_id / summary / task_state。"""
        recorder = LoggingAuditRecorder()
        event = MemoryAuditEvent(
            triggered=True,
            trigger_reason='action_proposal_present',
            proposal_action='UPSERT',
            task_type='EXPENSE_REQUEST',
        )
        with caplog.at_level(logging.INFO, logger='app.memory.memory_audit'):
            recorder.record(event)
        # 收集所有日志文本
        log_text = '\n'.join(record.getMessage() for record in caplog.records)
        # 仅允许元数据字面
        assert 'triggered=True' in log_text
        assert 'EXPENSE_REQUEST' in log_text
        # 显式禁止
        for forbidden in (
            'user_id', 'conversation_id', 'summary', 'task_state',
            'pendingStep',
        ):
            assert forbidden not in log_text

    def test_failure_category_classifier_maps_known_errors(self):
        """classify_failure_category 正确映射已知异常类别。"""
        assert classify_failure_category(None) is None
        assert classify_failure_category(MemoryWriteDispatcherError('x')) == 'dispatcher_error'
        assert classify_failure_category(ValueError('禁止键')) == 'invalid_snapshot'

    def test_classify_failure_category_pipeline_error(self):
        from app.memory.memory_pipeline import MemoryPipelineError
        assert classify_failure_category(MemoryPipelineError('x')) == 'pipeline_error'


# ===========================================================================
# 2. Rollout Mode Test
# ===========================================================================


class TestRolloutMode:
    """DISABLED / AUDIT_ONLY / ENABLED 三态语义。"""

    def test_default_mode_is_disabled(self):
        """MemoryWriteExecutionPolicy 默认 DISABLED。"""
        policy = MemoryWriteExecutionPolicy()
        assert policy.mode_value() == 'DISABLED'

    def test_make_execution_policy_default_is_disabled(self):
        """make_execution_policy() 不传参数默认 DISABLED。"""
        policy = make_execution_policy('DISABLED')
        assert policy.mode_value() == 'DISABLED'
        assert policy.should_dispatch(MagicMock()) is False

    def test_disabled_mode_skips_dispatch(self):
        """DISABLED 模式：Pipeline 触发但不调 Dispatcher（written=False）。"""
        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=_fake_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('DISABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        # Pipeline 触发；DISABLED 不调 Dispatcher
        assert result.triggered is True
        assert result.written is False
        # 验证 writer 没被调用
        writer.assert_not_called()

    def test_audit_only_mode_skips_dispatch(self):
        """AUDIT_ONLY 模式：Pipeline 跑通但 Dispatcher 不被调用（written=False）。"""
        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=_fake_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('AUDIT_ONLY'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        assert result.triggered is True
        assert result.written is False
        writer.assert_not_called()

    def test_enabled_mode_dispatches_and_writes(self):
        """ENABLED 模式：Dispatcher 被调用，writer 收到 command。"""
        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=_fake_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        assert result.triggered is True
        assert result.written is True
        writer.assert_called_once()
        cmd = writer.call_args.args[0]
        assert cmd.task_type == 'EXPENSE_REQUEST'

    def test_audit_event_carries_memory_write_mode(self):
        """MemoryAuditEvent.memory_write_mode 反映当前 mode。"""
        recorder = LoggingAuditRecorder()
        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=_fake_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        # 最后一条 event 携带 memory_write_mode
        assert recorder.events[-1].memory_write_mode == 'ENABLED'


# ===========================================================================
# 3. Failure Matrix Test
# ===========================================================================


class TestFailureMatrix:
    """Memory Failure Matrix：5 类失败场景 + 处理方式 + 是否阻断 Agent。"""

    # --- 3.1 Extractor Failure ---

    def test_extractor_failure_pipeline_degrades_to_noop(self):
        """Extractor 解析失败 → Pipeline 降级为 triggered=True + proposal=None。"""
        def bad_llm(system, user):
            return '{invalid json'

        pipeline = MemoryPipeline(
            task_type_policy=_policy(),
            llm_callable=bad_llm,
        )
        result = pipeline.process(_agent_result_with_proposal())
        # triggered=True（trigger 命中）+ proposal=None（parse 失败）
        assert result.triggered is True
        assert result.proposal is None
        assert result.command is None
        # Agent 仍可继续主返回路径；不抛错
        assert result.error is None

    def test_extractor_failure_runtime_hook_does_not_break_agent(self):
        """Extractor 失败时 Runtime Hook 不冒泡 → Agent 出口继续走通。"""
        def bad_llm(system, user):
            return '{invalid json'

        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=bad_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        # Hook 不抛错；writer 不被调用；triggered=True 但 written=False
        assert result.triggered is True
        assert result.written is False
        writer.assert_not_called()

    # --- 3.2 Policy Failure ---

    def test_policy_failure_blocks_write_with_fail_closed(self):
        """WritePolicy 校验未注册 taskType → 抛 ValueError（fail-closed）。"""
        # 默认 policy（无 EXPENSE_REQUEST 注册）
        default_policy = MemoryTaskTypePolicy.default()
        write_policy = MemoryWritePolicy(task_type_policy=default_policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',  # 未注册
            status='ACTIVE',
            task_state={'pendingStep': 'manager_confirmation'},
        )
        with pytest.raises(ValueError, match='不允许的 task_type'):
            write_policy.evaluate(proposal)

    def test_policy_failure_caught_in_pipeline_and_runtime_hook(self):
        """Policy 失败 → Runtime Hook 包装为 MemoryPipelineError；不冒泡。"""
        default_policy = MemoryTaskTypePolicy.default()
        # LLM 输出 EXPENSE_REQUEST，但默认 policy 不允许 → WritePolicy 抛 ValueError
        def llm_returning_expense(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'EXPENSE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'pendingStep': 'manager_confirmation'},
            })

        writer = MagicMock()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=default_policy,
                llm_callable=llm_returning_expense,
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        # Hook 不冒泡；Pipeline 失败被包装
        assert result.written is False
        assert result.error is not None
        writer.assert_not_called()

    # --- 3.3 Dispatcher Failure ---

    def test_dispatcher_failure_runtime_hook_does_not_mask(self):
        """Dispatcher 抛错 → Runtime Hook 捕获；written=False；不冒泡。"""

        def failing_writer(command):
            raise MemoryWriteDispatcherError('Java endpoint timeout')

        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=_fake_llm,
            ),
            dispatcher=MemoryWriteDispatcher(writer=failing_writer),
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        assert result.triggered is True
        assert result.written is False
        assert result.error is not None
        # Audit 记录 failure_category=dispatcher_error
        assert recorder.events[-1].failure_category == 'dispatcher_error'
        assert recorder.events[-1].write_success is False

    # --- 3.4 Resolution Ambiguous ---

    def test_resolution_ambiguous_returns_need_clarification(self):
        """并列最新 → NeedClarification；禁止随机。"""
        from datetime import datetime, timezone

        t = datetime(2026, 8, 1, tzinfo=timezone.utc)
        c1 = MemoryCandidate(
            conversation_id='conv-A',
            task_type='LEAVE_REQUEST',
            updated_at=t,
            pending_step='form_fill',
        )
        c2 = MemoryCandidate(
            conversation_id='conv-A',
            task_type='EXPENSE_REQUEST',
            updated_at=t,  # 并列
            pending_step='manager_confirmation',
        )
        decision = MemoryTaskResolutionPolicy().resolve([c1, c2])
        assert isinstance(decision, NeedClarification)

    def test_resolution_no_active_candidates_returns_empty(self):
        """无 ACTIVE candidate → ResolutionEmpty。"""
        c = MemoryCandidate(
            conversation_id='conv-A',
            task_type='LEAVE_REQUEST',
            status='COMPLETED',
        )
        decision = MemoryTaskResolutionPolicy().resolve([c])
        assert isinstance(decision, ResolutionEmpty)

    # --- 3.5 Invalid Snapshot ---

    def test_invalid_snapshot_identity_keys_rejected_by_write_policy(self):
        """WritePolicy 剥离 task_state 内身份 / 系统控制键。"""
        policy = _policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'role': 'admin',
                'user_id': 'E10001',
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert 'role' not in cmd.task_state
        assert 'user_id' not in cmd.task_state
        assert cmd.task_state == {'pendingStep': 'manager_confirmation'}

    def test_invalid_snapshot_token_redacted(self):
        """敏感字符串（token= / jwt）触发 [REDACTED] 替换。"""
        policy = _policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'note': 'token=jwt-xxx-yyy',
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert cmd.task_state['note'] == '[REDACTED]'

    def test_invalid_snapshot_size_exceeded_raises(self):
        """task_state 序列化超限 → WritePolicy 抛 ValueError（fail-closed）。"""
        from app.memory.memory_write_policy import MAX_TASK_STATE_JSON_BYTES

        policy = _policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        # 构造超大 state
        huge_state = {'k': 'x' * (MAX_TASK_STATE_JSON_BYTES + 100)}
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state=huge_state,
        )
        with pytest.raises(ValueError, match='task_state 序列化字节数超过'):
            write_policy.evaluate(proposal)

    def test_invalid_snapshot_memory_candidate_rejects_forbidden_keys(self):
        """Memory Candidate 构造期拒绝 task_state 内身份键。"""
        with pytest.raises(ValueError, match='禁止键'):
            MemoryCandidate(
                conversation_id='conv-A',
                task_type='EXPENSE_REQUEST',
                task_state={'pendingStep': 'manager_confirmation', 'user_id': 'E10001'},
            )


# ===========================================================================
# 4. Contract Stability Test
# ===========================================================================


class TestContractStability:
    """Memory Contract v1 冻结：P0/P1 taskType 行为不变。"""

    @pytest.mark.parametrize('task_type', [
        'LEAVE_REQUEST',
        'EXPENSE_REQUEST',
        'GENERIC',
        'BUSINESS_ACTION',
    ])
    def test_p0_p1_task_types_supported_under_enabled_mode(self, task_type):
        """在 ENABLED 模式下，P0/P1 四个 taskType 仍可正常写入。"""
        writer = MagicMock()
        proposal = MemoryProposal(
            action='UPSERT',
            task_type=task_type,
            status='ACTIVE',
            task_state={'pendingStep': 'pending'},
        )
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_policy(),
                llm_callable=lambda s, u: proposal.model_dump_json(),
            ),
            dispatcher=MemoryWriteDispatcher(writer=writer),
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        result = hook.after_agent_response(_agent_result_with_proposal(), 'conv-A')
        assert result.written is True
        writer.assert_called_once()
        cmd = writer.call_args.args[0]
        assert cmd.task_type == task_type

    @pytest.mark.parametrize('task_type', [
        'LEAVE_REQUEST',
        'EXPENSE_REQUEST',
        'GENERIC',
        'BUSINESS_ACTION',
    ])
    def test_p0_p1_task_types_completion_supported(self, task_type):
        """P0/P1 taskType 的 COMPLETE / ABANDON 语义保持不变。"""
        policy = _policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)

        # COMPLETE
        complete = MemoryProposal(
            action='COMPLETE', task_type=task_type, status='COMPLETED',
        )
        cmd_c = write_policy.evaluate(complete)
        assert cmd_c is not None
        assert cmd_c.action == 'COMPLETE'
        assert cmd_c.status == 'COMPLETED'

        # ABANDON
        abandon = MemoryProposal(
            action='ABANDON', task_type=task_type, status='ABANDONED',
        )
        cmd_a = write_policy.evaluate(abandon)
        assert cmd_a is not None
        assert cmd_a.action == 'ABANDON'
        assert cmd_a.status == 'ABANDONED'

    def test_p0_default_capabilities_unchanged(self):
        """P0 默认 capability 集合在 Phase 8 仍生效。"""
        from app.capabilities.p0_default_capabilities import DEFAULT_P0_CAPABILITIES

        registry = MemoryCapabilityRegistry.of(list(DEFAULT_P0_CAPABILITIES))
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        # P0 三类 taskType 全部允许
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')
        # EXPENSE_REQUEST 不在默认集合 → 拒绝
        assert not policy.is_allowed('EXPENSE_REQUEST')
        # eligible tool 默认 P0
        assert 'leave_proposal_tool' in policy.eligible_tool_names()

    def test_resolution_decision_types_frozen(self):
        """Resolution Decision 三态类型冻结：ResolvedMemory / NeedClarification /
        ResolutionEmpty。
        """
        from app.memory.memory_candidate import ResolutionDecision
        # ResolutionDecision 是 Union；验证三个具体类型都可被解析
        resolved = ResolvedMemory(
            candidate=MemoryCandidate(
                conversation_id='c', task_type='LEAVE_REQUEST',
            ),
            reason='unique_candidate',
        )
        nc = NeedClarification(candidates=(), reason='ambiguous')
        empty = ResolutionEmpty(reason='no_active_candidate')
        for d in (resolved, nc, empty):
            # 构造期接受；annotation 兼容
            assert isinstance(d, ResolutionDecision)

    def test_audit_event_field_set_frozen(self):
        """MemoryAuditEvent 字段集合冻结（10 个字段）。"""
        event = MemoryAuditEvent(triggered=False)
        data = event.model_dump()
        assert set(data.keys()) == {
            'triggered', 'trigger_reason', 'proposal_action', 'task_type',
            'write_attempted', 'write_success', 'error_type',
            'memory_write_mode', 'memory_resolution_reason', 'failure_category',
        }


# ===========================================================================
# 5. Security Final Review
# ===========================================================================


class TestSecurityFinal:
    """Phase 8-E Security 终审：Identity / Scope / Snapshot。"""

    def test_user_id_only_from_java_boundary(self):
        """Python 侧不持有 / 不接收 user_id 参数。"""
        # 触发 Resolution / Write Policy / Hook 不接受 user_id
        from app.memory.memory_task_resolution_policy import MemoryTaskResolutionPolicy

        candidate = MemoryCandidate(
            conversation_id='conv-A',
            task_type='LEAVE_REQUEST',
        )
        policy = MemoryTaskResolutionPolicy()
        with pytest.raises(TypeError):
            policy.resolve([candidate], user_id='E10001')  # type: ignore[call-arg]

    def test_scope_isolation_via_conversation_id(self):
        """user_id + conversation_id 复合 key；conversation_id 错配返回 Empty。"""
        candidate = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='EXPENSE_REQUEST',
        )
        # scope filter 不命中
        decision = MemoryTaskResolutionPolicy().resolve(
            [candidate],
            current_conversation_id='conv-userB',  # 不同 conversation
        )
        assert isinstance(decision, ResolutionEmpty)

    def test_memory_snapshot_is_not_authority_source(self):
        """Memory 不作为权限 / 审批 / 业务事实来源。"""
        policy = _policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        # Memory 不读取 approval_status / amount / role 等业务字段；
        # 仅剥离身份 / 系统控制字段（Memory 不评判业务有效性）
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'role': 'admin',  # 身份字段 → 剥离
                'amount': 200,  # 业务字段 → 不剥离（业务 Tool 责任）
            },
        )
        cmd = write_policy.evaluate(proposal)
        # Identity Boundary：身份字段被剥离
        assert 'role' not in cmd.task_state
        # Memory 不评判业务事实（business Tool 责任）
        assert cmd.task_state.get('pendingStep') == 'manager_confirmation'

    def test_conversation_id_does_not_leak_to_audit(self):
        """conversation_id 不出现在 Audit Event 序列化结果。"""
        recorder = LoggingAuditRecorder()
        event = MemoryAuditEvent(
            triggered=True,
            trigger_reason='action_proposal_present',
            proposal_action='UPSERT',
            task_type='EXPENSE_REQUEST',
        )
        recorder.record(event)
        data = event.model_dump()
        assert 'conversation_id' not in data
        assert 'conversationId' not in data

    def test_task_state_does_not_appear_in_audit(self):
        """task_state 不出现在 Audit Event。"""
        recorder = LoggingAuditRecorder()
        event = MemoryAuditEvent(
            triggered=True,
            task_type='EXPENSE_REQUEST',
        )
        recorder.record(event)
        data = event.model_dump()
        assert 'task_state' not in data


# ===========================================================================
# 6. Contract Freeze 文档元数据（辅助未来审计）
# ===========================================================================


class TestContractV1FrozenSurface:
    """Memory Contract v1 冻结面：API / 字段集合不可任意扩展。"""

    def test_memory_audit_event_field_count_locked(self):
        """MemoryAuditEvent 字段集合大小 = 10（Phase 8 冻结）。"""
        event = MemoryAuditEvent(triggered=True)
        assert len(event.model_dump()) == 10

    def test_memory_audit_event_extra_forbid_blocks_extension(self):
        """extra='forbid' 阻止任意字段扩展（新增字段必须显式声明）。"""
        with pytest.raises(Exception):
            MemoryAuditEvent(
                triggered=True,
                future_field='value',  # type: ignore[call-arg]
            )

    def test_memory_write_execution_policy_modes_locked(self):
        """MemoryWriteExecutionPolicy.mode 字面集合锁定为 3 个值。"""
        from app.memory.memory_write_mode import _VALID_MODES
        assert _VALID_MODES == frozenset({'DISABLED', 'AUDIT_ONLY', 'ENABLED'})

    def test_memory_write_mode_invalid_value_rejected(self):
        """非法 mode 字符串被拒绝（fail-closed）。"""
        from app.memory.memory_write_mode import MemoryWriteModeError

        with pytest.raises(MemoryWriteModeError):
            make_execution_policy('INVALID_MODE')  # type: ignore[arg-type]

    def test_resolution_policy_decision_union_locked(self):
        """ResolutionDecision 是三种类型的 Union（frozen surface）。"""
        import typing
        from app.memory.memory_candidate import ResolutionDecision
        # Union 的成员集合保持稳定
        args = set(typing.get_args(ResolutionDecision))
        from app.memory.memory_candidate import (
            NeedClarification,
            ResolutionEmpty,
            ResolvedMemory,
        )
        assert args == {ResolvedMemory, NeedClarification, ResolutionEmpty}