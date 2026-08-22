"""test_memory_p1c_task_resolution.py —— Memory P1-C Task Resolution Policy 测试

P1-C 目标：解决"多个 ACTIVE Memory 同时存在时，如何确定恢复哪个"。

本测试覆盖（与 P1-C 验收标准 1:1 对齐）：

  1. **Single Active Task**
     单条 ACTIVE 候选 → 直接返回 ResolvedMemory（无需 Rule 2/3/4）。

  2. **Multiple Active Task**
     多条 ACTIVE 候选 → latest updated_at 被选择。

  3. **Confirmation Priority（Rule 3）**
     含 ``pending_step == 'confirmation'`` 的候选优先于普通 latest。

  4. **Explicit Hint（Rule 2）**
     task_type hint 命中时，候选被收敛到 hint 子集。

  5. **Ambiguous Case（Rule 5）**
     多条候选无法收敛 → NeedClarification；**禁止随机**。

  6. **Isolation Regression**
     MemoryCandidate 不接受 user_id / tenant_id / role / permission 等身份字段；
     Resolution Policy API 不接收这些字段作为输入（caller 不会注入）。

  7. **Conversation Scope Match（Rule 1）**
     current_conversation_id 作为 in-scope filter 收敛候选。

  8. **Default Compatibility / P0 兼容**
     单 ACTIVE 候选 → Resolution 视为"直接恢复"（与 P0 Read Path 单 memory 行为一致）。

  9. **Capability Registry 集成（可选）**
     task_type hint 可经 Capability Registry 校验为已注册 capability；
     Resolution Policy 不依赖 Registry 也能工作。

 10. **Memory = Context Snapshot 边界**
     Resolution Policy 不触碰 task_state 内部业务字段（approval_status /
     expense_amount / leave_balance 等），仅消费元数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.capabilities.memory_capability import MemoryCapability
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.memory.memory_candidate import (
    MemoryCandidate,
    NeedClarification,
    ResolutionEmpty,
    ResolvedMemory,
)
from app.memory.memory_task_resolution_policy import (
    MemoryTaskResolutionPolicy,
    REASON_AMBIGUOUS_AFTER_FILTERS,
    REASON_AMBIGUOUS_NO_TIME,
    REASON_HINT_MATCHED_UNIQUE,
    REASON_LATEST_UPDATED,
    REASON_NO_ACTIVE_CANDIDATE,
    REASON_PENDING_CONFIRMATION,
    REASON_SCOPE_MATCHED_UNIQUE,
    REASON_UNIQUE_CANDIDATE,
)


# ===========================================================================
# Test helpers
# ===========================================================================


_T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
_T2 = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
_T3 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(
    task_type: str,
    *,
    conversation_id: str = 'conv-001',
    status: str = 'ACTIVE',
    task_state: dict | None = None,
    summary: str = '',
    created_at: datetime | None = _T0,
    updated_at: datetime | None = _T0,
    pending_step: str | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        conversation_id=conversation_id,
        task_type=task_type,
        status=status,
        task_state=task_state,
        summary=summary,
        created_at=created_at,
        updated_at=updated_at,
        pending_step=pending_step,
    )


@pytest.fixture
def policy() -> MemoryTaskResolutionPolicy:
    return MemoryTaskResolutionPolicy()


# ===========================================================================
# 1. Single Active Task
# ===========================================================================


class TestSingleActiveTask:
    def test_single_active_returns_resolved_directly(self, policy):
        """单条 ACTIVE 候选 → 直接 ResolvedMemory；reason=unique_candidate。"""
        c = _candidate('LEAVE_REQUEST', summary='等待补充请假日期')
        decision = policy.resolve([c])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'
        assert decision.reason == REASON_UNIQUE_CANDIDATE

    def test_single_active_with_scope_returns_scope_unique(self, policy):
        c = _candidate('LEAVE_REQUEST', conversation_id='conv-A')
        decision = policy.resolve(
            [c], current_conversation_id='conv-A',
        )
        assert isinstance(decision, ResolvedMemory)
        assert decision.reason == REASON_SCOPE_MATCHED_UNIQUE

    def test_single_active_with_hint_returns_hint_unique(self, policy):
        c = _candidate('EXPENSE_REQUEST')
        decision = policy.resolve([c], task_type_hint='EXPENSE_REQUEST')
        assert isinstance(decision, ResolvedMemory)
        assert decision.reason == REASON_HINT_MATCHED_UNIQUE


# ===========================================================================
# 2. Multiple Active Task — latest updated
# ===========================================================================


class TestMultipleActiveTask:
    def test_latest_updated_at_wins(self, policy):
        leave = _candidate('LEAVE_REQUEST', updated_at=_T1, created_at=_T0)
        expense = _candidate('EXPENSE_REQUEST', updated_at=_T2, created_at=_T1)
        decision = policy.resolve([leave, expense])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'
        assert decision.reason == REASON_LATEST_UPDATED

    def test_order_in_list_does_not_affect_latest_selection(self, policy):
        """列表顺序不应影响最新选择；只看 updated_at。"""
        leave = _candidate('LEAVE_REQUEST', updated_at=_T3)
        expense = _candidate('EXPENSE_REQUEST', updated_at=_T2)
        # leave 排在前面但 updated 更晚 → 仍应选 leave
        decision = policy.resolve([leave, expense])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'


# ===========================================================================
# 3. Confirmation Priority
# ===========================================================================


class TestConfirmationPriority:
    def test_confirmation_wins_over_latest_updated(self, policy):
        """Rule 3：pending_step=confirmation 优先于 Rule 4 latest_updated。"""
        # expense 较新但不是 confirmation；leave 较旧但是 confirmation → 应选 leave
        expense = _candidate(
            'EXPENSE_REQUEST', updated_at=_T3,
            pending_step='form_fill',
        )
        leave = _candidate(
            'LEAVE_REQUEST', updated_at=_T2,
            pending_step='confirmation',
        )
        decision = policy.resolve([expense, leave])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'
        assert decision.reason == REASON_PENDING_CONFIRMATION

    def test_confirmation_latest_wins_among_confirmations(self, policy):
        """多条 confirmation 候选时，按 updated_at 取最新。"""
        a = _candidate('EXPENSE_REQUEST', updated_at=_T1, pending_step='confirmation')
        b = _candidate('EXPENSE_REQUEST', updated_at=_T3, pending_step='confirmation')
        c = _candidate('LEAVE_REQUEST', updated_at=_T2, pending_step='confirmation')
        decision = policy.resolve([a, b, c])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'
        assert decision.candidate.updated_at == _T3

    def test_no_confirmation_falls_through_to_latest(self, policy):
        """无 confirmation → 走 Rule 4 latest_updated。"""
        a = _candidate('LEAVE_REQUEST', updated_at=_T1, pending_step='form_fill')
        b = _candidate('EXPENSE_REQUEST', updated_at=_T3, pending_step='awaiting_receipt')
        decision = policy.resolve([a, b])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'
        assert decision.reason == REASON_LATEST_UPDATED


# ===========================================================================
# 4. Explicit Hint
# ===========================================================================


class TestExplicitHint:
    def test_hint_filters_candidates(self, policy):
        """Rule 2 hint：筛除 task_type 不匹配的候选。"""
        leave = _candidate('LEAVE_REQUEST', updated_at=_T3)
        expense = _candidate('EXPENSE_REQUEST', updated_at=_T2)
        decision = policy.resolve([leave, expense], task_type_hint='EXPENSE_REQUEST')
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'

    def test_hint_yields_empty_when_no_match(self, policy):
        """hint 不命中任何候选 → ResolutionEmpty。"""
        leave = _candidate('LEAVE_REQUEST')
        decision = policy.resolve([leave], task_type_hint='TRAVEL_REQUEST')
        assert isinstance(decision, ResolutionEmpty)
        assert decision.reason == REASON_NO_ACTIVE_CANDIDATE


# ===========================================================================
# 5. Ambiguous Case
# ===========================================================================


class TestAmbiguousCase:
    def test_ambiguous_no_time_signal_returns_need_clarification(self, policy):
        """多候选 + 全部 updated_at=None → NeedClarification（禁止随机）。"""
        a = _candidate('LEAVE_REQUEST', created_at=None, updated_at=None)
        b = _candidate('EXPENSE_REQUEST', created_at=None, updated_at=None)
        decision = policy.resolve([a, b])
        assert isinstance(decision, NeedClarification)
        assert decision.reason == REASON_AMBIGUOUS_NO_TIME
        assert {c.task_type for c in decision.candidates} == {
            'LEAVE_REQUEST', 'EXPENSE_REQUEST',
        }

    def test_ambiguous_tied_updated_at_returns_need_clarification(self, policy):
        """并列最新 → NeedClarification，不允许随机收敛。"""
        a = _candidate('LEAVE_REQUEST', updated_at=_T2)
        b = _candidate('EXPENSE_REQUEST', updated_at=_T2)
        decision = policy.resolve([a, b])
        assert isinstance(decision, NeedClarification)
        assert decision.reason == REASON_AMBIGUOUS_AFTER_FILTERS

    def test_ambiguous_after_hint_filter_still_ambiguous(self, policy):
        """hint 过滤后仍有多候选且并列 → NeedClarification。"""
        a = _candidate('LEAVE_REQUEST', updated_at=_T2)
        b = _candidate('LEAVE_REQUEST', updated_at=_T2)  # 同 task_type
        decision = policy.resolve([a, b], task_type_hint='LEAVE_REQUEST')
        # 都是 LEAVE_REQUEST + 同一 updated_at → NeedClarification
        assert isinstance(decision, NeedClarification)


# ===========================================================================
# 6. Isolation Regression
# ===========================================================================


class TestIsolationRegression:
    @pytest.mark.parametrize('forbidden_field,forbidden_value', [
        ('user_id', 'E10001'),
        ('userId', 'E10001'),
        ('employee_id', 'E10001'),
        ('tenant_id', 'T1'),
        ('role', 'ADMIN'),
        ('permission', 'eval'),
        ('allow_eval', True),
        ('token', 'jwt-xxx'),
        ('password', 'hunter2'),
    ])
    def test_memory_candidate_rejects_identity_field(
        self, forbidden_field, forbidden_value,
    ):
        with pytest.raises((ValueError, Exception)):
            MemoryCandidate(
                conversation_id='conv-A',
                task_type='EXPENSE_REQUEST',
                **{forbidden_field: forbidden_value},  # type: ignore[arg-type]
            )

    def test_memory_candidate_task_state_forbids_identity_keys(self):
        """task_state 内嵌 user_id 等必须被构造期拒绝（fail-loud）。"""
        with pytest.raises(ValueError, match='禁止键'):
            MemoryCandidate(
                conversation_id='conv-A',
                task_type='EXPENSE_REQUEST',
                task_state={
                    'waiting_for': 'receipt',
                    'user_id': 'E10001',  # forbidden
                },
            )

    def test_resolution_policy_does_not_accept_user_id_argument(self, policy):
        """Resolution Policy API 不接收 user_id / tenant_id 等参数（构造期防御）。"""
        # 显式尝试传入非法关键字参数 → TypeError
        with pytest.raises(TypeError):
            policy.resolve([], user_id='E10001')  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            policy.resolve([], tenant_id='T1')  # type: ignore[call-arg]

    def test_resolution_policy_serializes_only_metadata(self, policy):
        """ResolvedMemory 序列化结果只含 candidate + reason（无身份字段）。"""
        c = _candidate('LEAVE_REQUEST', summary='x')
        decision = policy.resolve([c])
        assert isinstance(decision, ResolvedMemory)
        data = decision.model_dump()
        # 顶层仅 candidate / reason
        assert set(data.keys()) == {'candidate', 'reason'}
        # candidate 内部不携带身份字段
        for forbidden in (
            'user_id', 'userId', 'employee_id', 'tenant_id', 'role',
            'permission', 'allow_eval', 'token',
        ):
            assert forbidden not in data['candidate']

    def test_resolution_does_not_inject_user_data_into_decision(self, policy):
        """即便 candidates 含业务上下文，Resolution 输出也不携带业务数据键。"""
        c = _candidate(
            'EXPENSE_REQUEST',
            task_state={'waiting_for': 'receipt', 'amount': 200},
        )
        decision = policy.resolve([c])
        # Resolution 输出保留 task_state（Context Snapshot）但不含 user_id
        # 等身份字段（由 MemoryCandidate 构造期阻断）。
        dumped = decision.model_dump()
        assert 'user_id' not in dumped['candidate']


# ===========================================================================
# 7. Conversation Scope Match（Rule 1）
# ===========================================================================


class TestConversationScopeMatch:
    def test_scope_filter_removes_out_of_scope(self, policy):
        a = _candidate('LEAVE_REQUEST', conversation_id='conv-A')
        b = _candidate('EXPENSE_REQUEST', conversation_id='conv-B')
        decision = policy.resolve([a, b], current_conversation_id='conv-A')
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'
        assert decision.candidate.conversation_id == 'conv-A'
        assert decision.reason == REASON_SCOPE_MATCHED_UNIQUE

    def test_scope_filter_no_match_returns_empty(self, policy):
        a = _candidate('LEAVE_REQUEST', conversation_id='conv-A')
        decision = policy.resolve([a], current_conversation_id='conv-B')
        assert isinstance(decision, ResolutionEmpty)


# ===========================================================================
# 8. Default Compatibility / P0 兼容
# ===========================================================================


class TestDefaultCompatibility:
    def test_single_active_equivalent_to_p0_resume(self, policy):
        """单 ACTIVE 候选 → Resolution 等价于 P0 Read Path "直接恢复"。"""
        c = _candidate('LEAVE_REQUEST', summary='等待补充请假日期')
        decision = policy.resolve([c])
        # P0 行为：单 ACTIVE 直接注入 AgentState.memory_context。
        # P1-C 行为：单 ACTIVE → ResolvedMemory（同一 memory），与 P0 语义一致。
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'
        assert decision.candidate.summary == '等待补充请假日期'

    def test_empty_input_returns_empty(self, policy):
        decision = policy.resolve([])
        assert isinstance(decision, ResolutionEmpty)
        assert decision.reason == REASON_NO_ACTIVE_CANDIDATE

    def test_non_active_candidates_filtered_out(self, policy):
        """COMPLETED / ABANDONED 视为不可恢复候选。"""
        a = _candidate('LEAVE_REQUEST', status='COMPLETED')
        b = _candidate('EXPENSE_REQUEST', status='ABANDONED')
        decision = policy.resolve([a, b])
        assert isinstance(decision, ResolutionEmpty)

    def test_mixed_active_and_completed(self, policy):
        """混合 ACTIVE + COMPLETED：仅 ACTIVE 参与。"""
        completed = _candidate('LEAVE_REQUEST', status='COMPLETED', updated_at=_T3)
        active = _candidate('EXPENSE_REQUEST', updated_at=_T1)
        decision = policy.resolve([completed, active])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'EXPENSE_REQUEST'


# ===========================================================================
# 9. Capability Registry 集成（可选加固）
# ===========================================================================


class TestCapabilityRegistryIntegration:
    def test_resolution_works_without_registry(self):
        """Resolution Policy 不依赖 Registry 也能工作。"""
        policy = MemoryTaskResolutionPolicy()
        c = _candidate('EXPENSE_REQUEST')
        decision = policy.resolve([c])
        assert isinstance(decision, ResolvedMemory)

    def test_resolution_accepts_registry_for_optional_validation(self):
        """注入 Registry 不破坏 Resolution 主路径（hint 命中即收敛）。"""
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        registry = MemoryCapabilityRegistry.of([expense])
        policy = MemoryTaskResolutionPolicy(capability_registry=registry)
        c = _candidate('EXPENSE_REQUEST')
        decision = policy.resolve([c], task_type_hint='EXPENSE_REQUEST')
        assert isinstance(decision, ResolvedMemory)
        # Registry 暴露给 policy 用于 audit / future validation
        assert policy.capability_registry is registry


# ===========================================================================
# 10. Memory = Context Snapshot 边界
# ===========================================================================


class TestContextSnapshotBoundary:
    def test_resolution_does_not_inspect_business_fields(self, policy):
        """Resolution Policy 不触碰 task_state 内部业务字段。"""
        candidates = [
            _candidate(
                'EXPENSE_REQUEST',
                task_state={
                    'waiting_for': 'receipt',
                    'amount': 200,           # 业务字段
                    'approval_status': 'pending',  # 业务字段
                },
                updated_at=_T2,
            ),
            _candidate(
                'LEAVE_REQUEST',
                task_state={
                    'waiting_for': 'date',
                    'leave_balance': 5,      # 业务字段
                    'manager_permission': True,  # 业务字段
                },
                updated_at=_T3,
            ),
        ]
        decision = policy.resolve(candidates)
        # 仅基于 updated_at / task_type / status 收敛；不读 amount / approval_status /
        # leave_balance / manager_permission。
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'

    def test_resolution_pure_function_no_side_effects(self, policy):
        """相同输入多次调用应返回相同结果；candidates 不被修改。"""
        candidates = [
            _candidate('LEAVE_REQUEST', updated_at=_T2),
            _candidate('EXPENSE_REQUEST', updated_at=_T1),
        ]
        results = [policy.resolve(candidates) for _ in range(3)]
        assert all(
            isinstance(r, ResolvedMemory) and r.candidate.task_type == 'LEAVE_REQUEST'
            for r in results
        )
        # 原始 candidates list 内容不变
        assert [c.task_type for c in candidates] == ['LEAVE_REQUEST', 'EXPENSE_REQUEST']

    def test_resolution_ignores_malformed_entries_safely(self, policy):
        """非 MemoryCandidate 实例混入候选列表时被静默跳过。"""
        c = _candidate('LEAVE_REQUEST')
        decision = policy.resolve([c, {'fake': 'dict'}, None, 'string'])
        assert isinstance(decision, ResolvedMemory)
        assert decision.candidate.task_type == 'LEAVE_REQUEST'


# ===========================================================================
# 11. updated_at 校验
# ===========================================================================


class TestUpdatedAtValidation:
    def test_candidate_rejects_updated_before_created(self):
        """updated_at 必须 ≥ created_at（构造期 fail-loud）。"""
        with pytest.raises(ValueError, match='updated_at'):
            MemoryCandidate(
                conversation_id='conv-A',
                task_type='EXPENSE_REQUEST',
                created_at=_T2,
                updated_at=_T1,
            )

    def test_candidate_accepts_updated_equal_to_created(self):
        c = MemoryCandidate(
            conversation_id='conv-A',
            task_type='EXPENSE_REQUEST',
            created_at=_T1,
            updated_at=_T1,
        )
        assert c.updated_at == _T1

    def test_candidate_accepts_only_updated_no_created(self):
        """只设 updated_at、created_at=None：合法。"""
        c = MemoryCandidate(
            conversation_id='conv-A',
            task_type='EXPENSE_REQUEST',
            created_at=None,
            updated_at=_T1,
        )
        assert c.updated_at == _T1


# ===========================================================================
# 12. 时间序列决策稳定
# ===========================================================================


class TestResolutionStability:
    def test_resolution_idempotent(self, policy):
        """pure-function：相同输入必返回相同输出。"""
        candidates = [
            _candidate('LEAVE_REQUEST', updated_at=_T1),
            _candidate('EXPENSE_REQUEST', updated_at=_T2),
        ]
        r1 = policy.resolve(candidates)
        r2 = policy.resolve(candidates)
        assert r1 == r2

    def test_resolution_does_not_mutate_input_candidates(self, policy):
        candidates = [
            _candidate('LEAVE_REQUEST', updated_at=_T2),
            _candidate('EXPENSE_REQUEST', updated_at=_T1),
        ]
        snapshot = [(c.task_type, c.updated_at) for c in candidates]
        policy.resolve(candidates)
        assert [(c.task_type, c.updated_at) for c in candidates] == snapshot

    def test_candidate_is_frozen(self):
        """MemoryCandidate frozen：构造后不可修改。"""
        c = _candidate('LEAVE_REQUEST')
        with pytest.raises(Exception):  # ValidationError on frozen model
            c.task_type = 'HACKED'  # type: ignore[misc]