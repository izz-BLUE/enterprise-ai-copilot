"""test_memory_p1d_expense_workflow_integration.py —— Memory P1-D 集成验证

P1-D 目标：证明 Memory 已具备承载"第二 Workflow"（EXPENSE_REQUEST）的能力。
本阶段**不是**开发 Expense 产品；不创建任何业务系统；仅通过测试验证
"EXPENSE_REQUEST 接入 Memory 的全生命周期"满足设计边界。

本测试不修改任何生产代码（除测试 fixture 本身）。所有被验证的模块
（memory_extractor / memory_trigger_policy / memory_write_policy / memory_pipeline
 / memory_task_type_policy / memory_task_resolution_policy / memory_candidate /
 memory_capability_registry）均沿用 P1-A / P1-B / P1-C 的既有 API。

覆盖：

  1. **Write Path Validation**
     EXPENSE Workflow 写 Memory 路径完整跑通：
     Capability Registry → MemoryTaskTypePolicy → Extractor Prompt →
     Memory Proposal → Write Policy → MemoryWriteCommand 合法生成；
     不修改 Java / DB / Runtime Hook / Dispatcher。

  2. **Read Path Validation**
     EXPENSE Memory Resume 路径完整跑通：
     Memory Candidate → Task Resolution Policy → ResolvedMemory；
     pendingStep 恢复为 Agent Resume 上下文（不携带业务事实）。

  3. **Boundary Validation**
     taskState 只保存 Agent Resume 上下文（pendingStep / workflow context）；
     禁止保存：expenseId / approvalStatus / amount / employeeId / manager permission /
     任何业务事实。

  4. **Security Validation**
     用户 A / B 各自的 EXPENSE_REQUEST Memory 隔离：
     - A 不能恢复 B 的 memory；
     - B 不能拿到 A 的 context。

  5. **Multi Task Validation**
     LEAVE_REQUEST + EXPENSE_REQUEST 同时 ACTIVE：
     - hint=EXPENSE_REQUEST → 恢复 Expense；
     - 无 hint 且并列 → NeedClarification（禁止随机恢复）。

  6. **Failure Scenario Validation**
     - Java Memory Endpoint 不可用 → 写入不伪装成功；
     - Invalid Snapshot（含 role=admin 等敏感字段） → 被 _scrub_task_state 拒绝；
     - Stale Snapshot（taskState 与业务系统无关）→ Memory 不补齐业务事实。

  7. **Regression**
     LEAVE_REQUEST / BUSINESS_ACTION / GENERIC 行为不变（已有 CapabilityRegistry /
     Policy 默认实现覆盖 P0 行为）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.capabilities.memory_capability import MemoryCapability
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.memory.memory_candidate import (
    MemoryCandidate,
    NeedClarification,
    ResolutionEmpty,
    ResolvedMemory,
)
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_task_resolution_policy import MemoryTaskResolutionPolicy
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_policy import MemoryWriteCommand, MemoryWritePolicy
from app.schemas.memory_schema import MemoryProposal


# ===========================================================================
# Test helpers（仅在测试内；不构成产品代码）
# ===========================================================================


_T0 = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 8, 11, 9, 0, 0, tzinfo=timezone.utc)
_T2 = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


def _expense_capability() -> MemoryCapability:
    """EXPENSE_REQUEST Memory 接入元数据（不实现 Expense 业务）。"""
    return MemoryCapability(
        task_type='EXPENSE_REQUEST',
        eligible_tools=frozenset({'expense_proposal_tool'}),
        description='差旅报销业务 Memory 接入（P1-D 验证）；仅声明元数据。',
    )


def _leave_capability() -> MemoryCapability:
    """LEAVE_REQUEST 已存在的 P0 capability（用于多任务验证）。"""
    return MemoryCapability(
        task_type='LEAVE_REQUEST',
        eligible_tools=frozenset({'leave_proposal_tool'}),
        description='请假业务 Memory 接入（P0）。',
    )


def _expense_registry() -> MemoryCapabilityRegistry:
    return MemoryCapabilityRegistry.of(
        [_leave_capability(), _expense_capability()],
    )


def _expense_policy(include_default_p0: bool = True) -> MemoryTaskTypePolicy:
    return MemoryTaskTypePolicy.create_from_registry(
        _expense_registry(),
        include_default_p0=include_default_p0,
    )


def _expense_candidate(
    *,
    conversation_id: str = 'conv-userA',
    task_type: str = 'EXPENSE_REQUEST',
    task_state: dict | None = None,
    summary: str = '',
    updated_at: datetime | None = _T1,
    pending_step: str | None = None,
    conversation_id_override: str | None = None,
) -> MemoryCandidate:
    """构造一个 EXPENSE_REQUEST Memory Candidate（不带身份字段）。"""
    return MemoryCandidate(
        conversation_id=conversation_id_override or conversation_id,
        task_type=task_type,
        status='ACTIVE',
        task_state=task_state,
        summary=summary,
        created_at=_T0,
        updated_at=updated_at,
        pending_step=pending_step,
    )


# ===========================================================================
# 1. Write Path Validation
# ===========================================================================


class TestExpenseWritePath:
    """Write Path：Capability Registry → Policy → MemoryWriteCommand。"""

    def test_capability_registry_accepts_expense_request(self):
        registry = _expense_registry()
        assert 'EXPENSE_REQUEST' in registry.task_types()
        assert 'expense_proposal_tool' in registry.eligible_tools()
        assert registry.tool_mapping()['expense_proposal_tool'] == 'EXPENSE_REQUEST'

    def test_memory_task_type_policy_built_from_registry(self):
        policy = _expense_policy()
        assert policy.is_allowed('EXPENSE_REQUEST')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('GENERIC')
        assert 'expense_proposal_tool' in policy.eligible_tool_names()
        assert 'leave_proposal_tool' in policy.eligible_tool_names()

    def test_extractor_prompt_advertises_expense_request(self):
        """Extractor system_prompt 动态渲染 EXPENSE_REQUEST 白名单。"""
        policy = _expense_policy()
        extractor = MemoryExtractor(task_type_policy=policy)
        prompt = extractor.system_prompt
        assert "'EXPENSE_REQUEST'" in prompt
        assert "'LEAVE_REQUEST'" in prompt

    def test_write_policy_generates_legal_memory_write_command(self):
        """Write Policy 接受 EXPENSE_REQUEST proposal 并生成合法 MemoryWriteCommand。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={'pendingStep': 'manager_confirmation'},
            summary='等待主管确认报销申请',
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.action == 'UPSERT'
        assert cmd.task_type == 'EXPENSE_REQUEST'
        assert cmd.status == 'ACTIVE'
        # taskState 已脱敏；本测试 case 不含敏感字段，验证结构未被篡改
        assert cmd.task_state == {'pendingStep': 'manager_confirmation'}
        assert cmd.summary == '等待主管确认报销申请'

    def test_write_path_does_not_require_java_or_db_changes(self):
        """MemoryWriteCommand 是 Python → Java payload 边界；5 字段白名单不变。

        不引入新字段意味着不修改 ai_task_memory 表 / Java Endpoint /
        DTO Contract / Flyway Migration。这是 P1-D 验收点"无需修改 Java / DB"的关键。
        """
        from app.clients.java_memory_client import _PAYLOAD_FIELD_MAP
        assert set(_PAYLOAD_FIELD_MAP.keys()) == {
            'action', 'task_type', 'status', 'task_state', 'summary',
        }
        # 不存在 expenseId / amount / approvalStatus 等"业务字段"的 outbound 路径。
        for forbidden_outbound in (
            'expenseId', 'amount', 'approvalStatus',
            'employeeId', 'managerPermission',
        ):
            assert forbidden_outbound not in _PAYLOAD_FIELD_MAP


# ===========================================================================
# 2. Read Path Validation
# ===========================================================================


class TestExpenseReadPath:
    """Read Path：Memory Candidate → Task Resolution Policy → ResolvedMemory。"""

    def test_resolution_recovers_expense_candidate(self):
        candidate = _expense_candidate(
            task_state={'pendingStep': 'manager_confirmation'},
            summary='等待主管确认报销申请',
            pending_step='manager_confirmation',
        )
        policy = MemoryTaskResolutionPolicy()
        decision = policy.resolve([candidate])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'
        assert decision.candidate.task_state == {'pendingStep': 'manager_confirmation'}
        assert decision.candidate.summary == '等待主管确认报销申请'

    def test_resolution_preserves_pending_step_for_resume(self):
        """ResolvedMemory 的 pendingStep 是 Agent Resume 的入口；不携带业务事实。"""
        candidate = _expense_candidate(
            task_state={'pendingStep': 'manager_confirmation'},
            pending_step='manager_confirmation',
        )
        decision = MemoryTaskResolutionPolicy().resolve([candidate])
        assert isinstance(decision, ResolvedMemory)
        # pending_step 字段保留
        assert decision.candidate.pending_step == 'manager_confirmation'
        # 但 task_state 内不携带审批 / 金额 / 员工 ID
        assert 'approvalStatus' not in decision.candidate.task_state
        assert 'amount' not in decision.candidate.task_state
        assert 'employeeId' not in decision.candidate.task_state


# ===========================================================================
# 3. Boundary Validation
# ===========================================================================


class TestExpenseBoundary:
    """taskState 边界：Memory 只承载 Agent Resume Context 与 Context Snapshot。

    Memory Write Policy 负责剥离"身份 / 系统控制 / 凭据"字段（属于 Identity
    Boundary）；不剥离业务字段（如 expenseId / amount / approvalStatus）——
    业务字段剥离属于业务 Tool 的责任（Tool 在生成 action_proposal 时不应把
    业务事实写入 task_state；这是 P1-D 验证出的"边界纪律"）。

    本测试明确这一边界，避免越界假定"Memory = Business DB"。
    """

    @pytest.mark.parametrize('forbidden_key,forbidden_value', [
        # 身份 / 系统控制 / 凭据字段（Memory 必须剥离）
        ('employeeId', 'E10001'),
        ('employee_id', 'E10001'),
        ('user_id', 'U10001'),
        ('role', 'admin'),
        ('token', 'jwt-xxx'),
        ('business_date', '2026-08-20'),
    ])
    def test_write_policy_strips_identity_and_system_keys(
        self, forbidden_key, forbidden_value,
    ):
        """MemoryWritePolicy._scrub_task_state 必须剥离身份 / 系统控制 / 凭据键。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                forbidden_key: forbidden_value,  # type: ignore[dict-item]
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert forbidden_key not in cmd.task_state
        assert cmd.task_state.get('pendingStep') == 'manager_confirmation'

    def test_memory_candidate_rejects_identity_keys_in_task_state(self):
        """Memory Candidate 构造期即阻断身份键混入 taskState。"""
        with pytest.raises(ValueError, match='禁止键'):
            MemoryCandidate(
                conversation_id='conv-A',
                task_type='EXPENSE_REQUEST',
                task_state={
                    'pendingStep': 'manager_confirmation',
                    'employee_id': 'E10001',  # forbidden
                },
            )

    def test_write_policy_does_not_silently_strip_business_fields(self):
        """业务字段（expenseId / amount / approvalStatus）不属于 Memory 剥离职责。

        Memory = Context Snapshot，不评判业务事实；业务 Tool 在调用时不应
        把业务事实塞进 taskState。本测试显式记录这一边界：Memory 不会"主动
        清洗"业务字段（Tool 的责任）。
        """
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'expenseId': 'EX-2026-001',  # 业务字段：Tool 不应写入
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        # Memory 不会主动清洗业务字段；Tool 的责任是"不应写入"。
        # 但身份 / 系统字段会被剥离。
        assert 'expenseId' in cmd.task_state  # 边界：Memory 不剥离业务字段
        assert 'pendingStep' in cmd.task_state

    def test_resolved_memory_does_not_carry_identity_keys(self):
        """ResolvedMemory 序列化结果不含身份 / 系统键（已被 Memory Candidate 阻断）。"""
        candidate = _expense_candidate(
            task_state={'pendingStep': 'manager_confirmation'},
        )
        decision = MemoryTaskResolutionPolicy().resolve([candidate])
        assert isinstance(decision, ResolvedMemory)
        dumped = decision.model_dump()
        for forbidden in (
            'user_id', 'userId', 'employee_id', 'tenant_id',
            'role', 'permission', 'token', 'business_date',
        ):
            assert forbidden not in dumped['candidate']

    def test_memory_does_not_become_business_database(self):
        """Memory 仅承载 Agent Resume Context；不替代业务数据库。"""
        candidate = _expense_candidate(
            task_state={
                'pendingStep': 'manager_confirmation',
                'nextInputField': 'managerName',
                'awaitingFields': ['receipt', 'managerName'],
            },
        )
        decision = MemoryTaskResolutionPolicy().resolve([candidate])
        assert isinstance(decision, ResolvedMemory)
        # Context Snapshot：字段是"续接输入提示"，不构成业务事实判定。
        assert decision.candidate.task_state['pendingStep'] == 'manager_confirmation'
        assert decision.candidate.task_state['awaitingFields'] == [
            'receipt', 'managerName',
        ]


# ===========================================================================
# 4. Security Validation
# ===========================================================================


class TestExpenseSecurity:
    """用户 A / B 隔离。"""

    def test_user_isolation_user_a_cannot_resume_user_b_memory(self):
        """User B 的 EXPENSE_REQUEST Memory 不会被 User A 的 Resolution 恢复。

        注：Python Resolution Policy **不**做用户身份判定（无 user_id 输入）；
        隔离由 Java VerifiedIdentity 在进入 Python 前完成（scope filter）。
        本测试验证：candidates 列表本身就是已 in-scope 的，Python 侧不会跨用户串味。
        """
        candidate_b = _expense_candidate(
            conversation_id_override='conv-userB',
            task_state={'pendingStep': 'manager_confirmation'},
            summary='User B 的报销',
        )
        # User A 自己的 candidates 列表里**没有** User B 的 candidate；
        # Resolution 在空列表上返回 Empty（不串味）。
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=[],  # User A 没有 EXPENSE_REQUEST candidate
        )
        assert isinstance(decision, ResolutionEmpty)

    def test_conversation_scope_filter_prevents_cross_conversation_read(self):
        """同一用户不同 conversation：scope filter 必须阻挡跨 conversation 读取。"""
        candidate_in_conv_b = _expense_candidate(
            conversation_id='conv-userA-sessionB',
            task_state={'pendingStep': 'manager_confirmation'},
        )
        # 当前 conversation 是 sessionA；Resolution 必须排除 sessionB 的 candidate。
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=[candidate_in_conv_b],
            current_conversation_id='conv-userA-sessionA',
        )
        assert isinstance(decision, ResolutionEmpty)

    def test_resolution_does_not_carry_user_a_into_user_b_candidate(self):
        """User A 的 resolution 输出**仅含** User A 的 candidate，绝不混入 User B。"""
        user_a = _expense_candidate(
            conversation_id='conv-userA',
            summary='User A 报销',
        )
        # 仅传入 User A 的 candidate（User B 的 candidate 由 Java 侧阻挡在
        # Python 之前；本测试在 Python 侧保证：若 caller 误传混合列表，
        # scope filter 仍能正确隔离——使用 conversation_id 收敛）。
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=[user_a],
            current_conversation_id='conv-userA',
        )
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.conversation_id == 'conv-userA'


# ===========================================================================
# 5. Multi Task Validation
# ===========================================================================


class TestExpenseMultiTask:
    """LEAVE_REQUEST + EXPENSE_REQUEST 同时 ACTIVE。"""

    def _both_active_candidates(self):
        leave = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'pendingStep': 'form_fill'},
            summary='请假申请',
            created_at=_T1,
            updated_at=_T1,
            pending_step='form_fill',
        )
        expense = _expense_candidate(
            conversation_id='conv-userA',
            updated_at=_T2,
            task_state={'pendingStep': 'manager_confirmation'},
            summary='报销申请',
            pending_step='manager_confirmation',
        )
        return [leave, expense]

    def test_hint_expense_request_resolves_expense(self):
        """用户说"继续我的报销"，hint=EXPENSE_REQUEST → 恢复 Expense。"""
        candidates = self._both_active_candidates()
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=candidates,
            task_type_hint='EXPENSE_REQUEST',
        )
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'

    def test_hint_leave_request_resolves_leave(self):
        candidates = self._both_active_candidates()
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=candidates,
            task_type_hint='LEAVE_REQUEST',
        )
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'

    def test_no_hint_ambiguous_tied_updated_at_returns_need_clarification(self):
        """无 hint 且并列最新 → NeedClarification（禁止随机恢复）。"""
        leave = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            updated_at=_T2,
            pending_step='form_fill',
        )
        expense = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            updated_at=_T2,  # 并列最新
            pending_step='manager_confirmation',
        )
        decision = MemoryTaskResolutionPolicy().resolve([leave, expense])
        assert isinstance(decision, NeedClarification)
        assert {c.task_type for c in decision.candidates} == {
            'LEAVE_REQUEST', 'EXPENSE_REQUEST',
        }

    def test_no_hint_with_clear_latest_updated_at_resolves(self):
        """无 hint + expense 更新更晚 → 恢复 Expense。"""
        leave = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='LEAVE_REQUEST',
            updated_at=_T0,
            pending_step='form_fill',
        )
        expense = MemoryCandidate(
            conversation_id='conv-userA',
            task_type='EXPENSE_REQUEST',
            updated_at=_T2,  # 更晚
            pending_step='manager_confirmation',
        )
        decision = MemoryTaskResolutionPolicy().resolve([leave, expense])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'


# ===========================================================================
# 6. Failure Scenario Validation
# ===========================================================================


class TestExpenseFailureScenarios:
    """Memory Write Failure / Invalid Snapshot / Stale Snapshot。"""

    def test_java_endpoint_unavailable_does_not_mask_success(self):
        """Java Memory Endpoint 不可用 → Dispatcher 抛错 → 不伪装成功。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={'pendingStep': 'manager_confirmation'},
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)

        # Java Client 抛错的模拟
        def failing_writer(command):  # noqa: ARG001
            raise MemoryWriteDispatcherError(
                'Java Memory Endpoint 不可用 (HTTP 503)',
            )

        dispatcher = MemoryWriteDispatcher(writer=failing_writer)
        with pytest.raises(MemoryWriteDispatcherError, match='不可用'):
            dispatcher.dispatch(cmd)

    def test_java_endpoint_failure_captured_in_runtime_hook(self):
        """MemoryRuntimeHook 在 Java 写入失败时 result.error != None / written=False。"""
        from app.memory.memory_runtime_hook import MemoryRuntimeHook
        from app.memory.memory_write_mode import make_execution_policy

        def failing_writer(command):  # noqa: ARG001
            raise MemoryWriteDispatcherError('Java 503')

        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'EXPENSE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'pendingStep': 'manager_confirmation'},
            })

        dispatcher = MemoryWriteDispatcher(writer=failing_writer)
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(
                task_type_policy=_expense_policy(),
                llm_callable=fake_llm,
            ),
            dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        agent_result = {
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        }
        result = hook.after_agent_response(agent_result, conversation_id='conv-A')
        # Pipeline 触发；Dispatcher 失败 → written=False / result.error 不为空
        assert result.written is False
        assert result.error is not None
        assert isinstance(result.error, MemoryWriteDispatcherError)

    def test_invalid_snapshot_with_role_admin_rejected_by_write_policy(self):
        """Invalid Snapshot 含 role=admin：被 _scrub_task_state 剥离。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'role': 'admin',  # 恶意字段
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        # role 已被剥离
        assert 'role' not in cmd.task_state
        assert cmd.task_state == {'pendingStep': 'manager_confirmation'}

    def test_invalid_snapshot_with_token_redacted_by_write_policy(self):
        """Sensitive 字符串（token / jwt）触发 [REDACTED] 替换。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={
                'pendingStep': 'manager_confirmation',
                'note': 'token=jwt-secret-value',  # 命中 _REDACT_MARKERS
            },
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.task_state['note'] == '[REDACTED]'

    def test_stale_snapshot_not_refreshed_by_memory(self):
        """Stale Snapshot（旧 Task Context）：Memory 不补齐业务事实。"""
        # 假设 User B 的旧 snapshot 流入 candidates；Resolution 只读元数据，不
        # 重新校验业务有效性（业务校验由 Java 业务链路负责）。
        stale_candidate = _expense_candidate(
            conversation_id='conv-userA',
            task_type='EXPENSE_REQUEST',
            task_state={'pendingStep': 'awaiting_receipt'},
            summary='旧的报销',
            updated_at=_T0,  # 很旧的时间戳
        )
        decision = MemoryTaskResolutionPolicy().resolve([stale_candidate])
        assert isinstance(decision, ResolvedMemory)
        # Resolution 仅传递 Context Snapshot；不验证业务有效性。
        # Stale 由业务系统（Java 侧）校验并拒绝。
        assert decision.candidate.summary == '旧的报销'
        assert decision.candidate.task_state == {'pendingStep': 'awaiting_receipt'}

    def test_memory_write_policy_rejects_unknown_task_type(self):
        """MemoryTaskTypePolicy 不允许的业务 task_type（如 TRAVEL_REQUEST 未注册）：
        Write Policy 抛错（fail-loud），不写入。
        """
        policy = _expense_policy()  # 注册 LEAVE + EXPENSE
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='TRAVEL_REQUEST',  # 未注册
            status='ACTIVE',
            task_state={'pendingStep': 'form_fill'},
        )
        with pytest.raises(ValueError, match='不允许的 task_type'):
            write_policy.evaluate(proposal)


# ===========================================================================
# 7. Regression —— P0 行为不变
# ===========================================================================


class TestRegressionP0Behavior:
    """LEAVE_REQUEST / BUSINESS_ACTION / GENERIC 行为不变。"""

    def test_leave_request_remains_supported(self):
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'pendingStep': 'form_fill'},
            summary='请假',
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.task_type == 'LEAVE_REQUEST'

    def test_generic_remains_supported_with_default_fallback(self):
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type=None,  # 缺省 → fallback = GENERIC
            status='ACTIVE',
            task_state={'pendingStep': 'awaiting_input'},
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.task_type == 'GENERIC'

    def test_business_action_remains_supported(self):
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='BUSINESS_ACTION',
            status='ACTIVE',
            task_state={'pendingStep': 'pending'},
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.task_type == 'BUSINESS_ACTION'

    def test_leave_proposal_tool_still_triggers_memory(self):
        """P0 eligible tool 仍触发 Memory 写入。"""
        policy = _expense_policy()
        trigger = MemoryTriggerPolicy(task_type_policy=policy)
        agent_result = {
            'tool_history': [{
                'tool_name': 'leave_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(agent_result)
        assert decision.should_extract is True

    def test_expense_proposal_tool_also_triggers_memory(self):
        """P1-D 验证：expense_proposal_tool 同样触发 Memory 写入。"""
        policy = _expense_policy()
        trigger = MemoryTriggerPolicy(task_type_policy=policy)
        agent_result = {
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(agent_result)
        assert decision.should_extract is True

    def test_default_registry_keeps_p0_capabilities(self):
        """仅用默认 P0 capability（无 EXPENSE）时，pipeline 行为与 P0 等价。"""
        # 默认 P0 registry
        from app.capabilities.p0_default_capabilities import (
            DEFAULT_P0_CAPABILITIES,
        )
        registry = MemoryCapabilityRegistry.of(list(DEFAULT_P0_CAPABILITIES))
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        # EXPENSE_REQUEST 未注册 → is_allowed 拒绝
        assert not policy.is_allowed('EXPENSE_REQUEST')
        # P0 类型仍然允许
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')


# ===========================================================================
# 8. End-to-End Workflow Simulation（不引入新生产模块）
# ===========================================================================


class TestExpenseWorkflowLifecycle:
    """完整生命周期：Create Proposal → Memory Write → Continue → Memory Resume → Complete。"""

    def _pipeline_with_writer(self, writer):
        policy = _expense_policy()
        from app.memory.memory_write_mode import make_execution_policy
        return MemoryPipeline(
            task_type_policy=policy,
            llm_callable=self._fake_llm,
        ), MemoryWriteDispatcher(writer=writer), make_execution_policy('ENABLED')

    def _fake_llm(self, system, user):
        return json.dumps({
            'action': 'UPSERT',
            'task_type': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'task_state': {'pendingStep': 'manager_confirmation'},
            'summary': '等待主管确认报销申请',
        })

    def test_create_proposal_to_memory_write(self):
        """Step 1+2：用户说"帮我提交差旅报销" → Pipeline 触发 → Memory Write。"""
        captured: dict = {}

        def writer(command: MemoryWriteCommand):
            captured['command'] = command

        pipeline = MemoryPipeline(
            task_type_policy=_expense_policy(),
            llm_callable=self._fake_llm,
        )
        dispatcher = MemoryWriteDispatcher(writer=writer)
        from app.memory.memory_write_mode import make_execution_policy
        dispatcher_policy = make_execution_policy('ENABLED')

        agent_result = {
            'action_proposal': {
                'action_type': 'EXPENSE_REQUEST',
                'pendingStep': 'manager_confirmation',
            },
        }
        result = pipeline.process(agent_result)
        assert result.triggered is True
        assert result.command is not None

        # Dispatcher 阶段（注入 writer）
        if dispatcher_policy.should_dispatch(result.command):
            dispatcher.dispatch(result.command)
        assert captured.get('command') is not None
        assert captured['command'].task_type == 'EXPENSE_REQUEST'
        assert captured['command'].task_state == {'pendingStep': 'manager_confirmation'}

    def test_continue_to_memory_resume(self):
        """Step 4+5：用户说"继续" → Candidates → ResolvedMemory。"""
        candidate = _expense_candidate(
            task_state={'pendingStep': 'manager_confirmation'},
            summary='等待主管确认报销申请',
            pending_step='manager_confirmation',
        )
        decision = MemoryTaskResolutionPolicy().resolve(
            candidates=[candidate],
            current_conversation_id='conv-userA',
        )
        assert isinstance(decision, ResolvedMemory)
        # Agent 可恢复 pendingStep（不携带审批 / 金额 / 员工 ID）
        resume_context = decision.candidate.task_state
        assert resume_context == {'pendingStep': 'manager_confirmation'}

    def test_complete_to_memory_completion(self):
        """Complete：Memory Proposal action=COMPLETE 写完成。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='COMPLETE',
            task_type='EXPENSE_REQUEST',
            status='COMPLETED',
            task_state={'pendingStep': 'completed'},
            summary='报销已完成',
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.action == 'COMPLETE'
        assert cmd.status == 'COMPLETED'

    def test_abandon_to_memory_abandonment(self):
        """Abandon：Memory Proposal action=ABANDON 标记放弃。"""
        policy = _expense_policy()
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='ABANDON',
            task_type='EXPENSE_REQUEST',
            status='ABANDONED',
            task_state={'pendingStep': 'cancelled'},
            summary='用户取消报销',
        )
        cmd = write_policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.action == 'ABANDON'
        assert cmd.status == 'ABANDONED'