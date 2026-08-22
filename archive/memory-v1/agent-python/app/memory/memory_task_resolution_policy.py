"""memory_task_resolution_policy.py —— Memory Task Resolution Policy（P1-C）

P1-C 解决"多个 ACTIVE Memory 同时存在时，如何确定当前请求应该恢复哪个"。

设计目标：

  Memory Candidate Set
        |
        v
  MemoryTaskResolutionPolicy
        |
        v
  ResolvedMemory | NeedClarification | ResolutionEmpty

Resolution 规则（严格按以下顺序；任一规则收敛即返回，不继续向下）：

  Rule 1  Conversation Scope Match（in-scope filter）
          —— 若 caller 提供 current_conversation_id，筛除 conversation_id 不匹配的候选。
          不会"选择"某一条，只缩小范围。
          无 current_conversation_id 时跳过（保留全部候选）。

  Rule 2  Explicit User Hint（task_type hint filter）
          —— 若 caller 提供 task_type hint，筛除 task_type 不匹配的候选。
          不会"选择"某一条，只缩小范围。
          无 hint 时跳过。

  Rule 3  Pending Confirmation Priority
          —— 若筛后候选中存在 ``pending_step == 'confirmation'`` 的子集，
          优先选择其中按 updated_at DESC 排序的最新一条。

  Rule 4  Latest Active Task
          —— 否则，取筛后候选中按 updated_at DESC 排序的最新一条；
          若 updated_at 全为 None，按 task_type 字典序取稳定顺序（避免随机）。

  Rule 5  Ambiguous
          —— 筛后候选数 ≥ 2 且无法收敛到唯一解 → NeedClarification。
          **禁止随机选择**。

边界纪律（不评判业务事实）：

  * Resolution Policy **不** 解析 task_state 内部业务字段（不读 approval_status /
    expense_amount / leave_balance / manager_permission 等）；
  * Resolution Policy **不** 决策"该任务是否仍然有效"（由业务 DB / Java 业务链路负责）；
  * Resolution Policy **不** 引入语义相似度 / Embedding / Vector Search / Ranking Platform；
  * Resolution Policy 仅消费 MemoryCandidate 元数据（task_type / status / pending_step /
    updated_at / created_at / summary / task_state-as-snapshot）。

可审计：

  * 每条规则的决策 reason 都写入 ResolvedMemory.reason / NeedClarification.reason；
  * 决策 pure-function，相同输入必返回相同输出；
  * 不修改输入 candidates（frozen）。

不引入：

  * Vector Search / Embedding / Semantic Similarity；
  * User Profile Memory / Preference Memory / Autonomous Memory Agent；
  * Memory Ranking Platform / 业务评估能力。
"""

from __future__ import annotations

from typing import Iterable

from app.memory.memory_candidate import (
    MemoryCandidate,
    NeedClarification,
    ResolutionDecision,
    ResolutionEmpty,
    ResolvedMemory,
)


# ---------------------------------------------------------------------------
# 决策 reason 常量（debug / 审计；业务逻辑不得依赖）
# ---------------------------------------------------------------------------

REASON_UNIQUE_CANDIDATE = 'unique_candidate'
REASON_SCOPE_MATCHED_UNIQUE = 'scope_matched_unique'
REASON_HINT_MATCHED_UNIQUE = 'hint_matched_unique'
REASON_PENDING_CONFIRMATION = 'pending_confirmation_latest'
REASON_LATEST_UPDATED = 'latest_updated_at'

REASON_AMBIGUOUS_AFTER_FILTERS = 'ambiguous_after_filters'
REASON_AMBIGUOUS_NO_TIME = 'ambiguous_no_time_signal'

REASON_NO_ACTIVE_CANDIDATE = 'no_active_candidate'


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class MemoryTaskResolutionPolicy:
    """Memory Task Resolution Policy。

    用法：
      policy = MemoryTaskResolutionPolicy()
      decision = policy.resolve(
          candidates=[...],
          current_conversation_id='conv-xxx',  # 可选：Rule 1
          task_type_hint='EXPENSE_REQUEST',      # 可选：Rule 2 hint
      )
      # decision: ResolvedMemory | NeedClarification | ResolutionEmpty

    可选注入：
      capability_registry —— 用于验证"hint task_type 是否已注册 capability"；
        注：Resolution Policy **不**依赖 Registry 也能工作；这是可选加固。
    """

    def __init__(self, capability_registry=None) -> None:
        # capability_registry 仅作为可选加固；接受 None 表示跳过 hint 注册校验。
        self._capability_registry = capability_registry

    @property
    def capability_registry(self):
        return self._capability_registry

    def resolve(
        self,
        candidates: Iterable[MemoryCandidate],
        current_conversation_id: str | None = None,
        task_type_hint: str | None = None,
    ) -> ResolutionDecision:
        """根据候选列表 + 可选 hint 收敛到唯一 ResolvedMemory / NeedClarification / Empty。

        参数：
          candidates              —— 待恢复候选列表（任意可迭代对象）；
                                     Resolution Policy 会先做 immutable 复制并冻结。
          current_conversation_id —— Rule 1 可选 in-scope 过滤；None 表示不限定。
          task_type_hint          —— Rule 2 可选 task_type hint；None 表示不限定。
                                     hint **不**作为权限来源，仅作为过滤条件。

        返回：
          ResolvedMemory         —— 唯一收敛结果；
          NeedClarification      —— 多条候选无法收敛；
          ResolutionEmpty        —— 无可恢复候选（空 / 全部非 ACTIVE）。
        """
        # 1. 收集候选 → 过滤非 ACTIVE → 防御性去重（frozen）
        working: list[MemoryCandidate] = [
            c for c in candidates if isinstance(c, MemoryCandidate) and c.is_recoverable()
        ]
        if not working:
            return ResolutionEmpty(reason=REASON_NO_ACTIVE_CANDIDATE)

        # 2. Rule 1: conversation_id in-scope filter
        if current_conversation_id:
            scoped = [
                c for c in working
                if c.conversation_id == current_conversation_id
            ]
            if not scoped:
                # scope filter 后无候选 → 视为无候选
                return ResolutionEmpty(reason=REASON_NO_ACTIVE_CANDIDATE)
            working = scoped

        # 3. Rule 2: explicit user hint（task_type hint）filter
        #    hint 不作为权限来源，仅作为过滤。
        if task_type_hint:
            hinted = [
                c for c in working
                if c.task_type == task_type_hint
            ]
            if not hinted:
                # hint 未命中任何候选 → 仍以 hint 为最强信号（题目意图）：
                # "继续我的报销申请"但 memory 中不存在 EXPENSE_REQUEST 候选，
                # 返回 Empty（而非 NeedClarification —— 无可恢复资源）。
                return ResolutionEmpty(reason=REASON_NO_ACTIVE_CANDIDATE)
            working = hinted

        # 4. 单候选 → 直接返回（无需 Rule 3/4）
        if len(working) == 1:
            only = working[0]
            reason = (
                REASON_HINT_MATCHED_UNIQUE if task_type_hint
                else REASON_SCOPE_MATCHED_UNIQUE if current_conversation_id
                else REASON_UNIQUE_CANDIDATE
            )
            return ResolvedMemory(candidate=only, reason=reason)

        # 5. Rule 3: pending_step == 'confirmation' 优先
        confirmation_set = [
            c for c in working if c.pending_step == 'confirmation'
        ]
        if confirmation_set:
            chosen = self._select_latest(confirmation_set)
            if chosen is None:
                return NeedClarification(
                    candidates=tuple(confirmation_set),
                    reason=REASON_AMBIGUOUS_NO_TIME,
                )
            return ResolvedMemory(
                candidate=chosen,
                reason=REASON_PENDING_CONFIRMATION,
            )

        # 6. Rule 4: latest updated_at
        chosen = self._select_latest(working)
        if chosen is None:
            # 多候选 + 无 updated_at 信号 → Rule 5: NeedClarification（禁止随机）
            return NeedClarification(
                candidates=tuple(working),
                reason=REASON_AMBIGUOUS_NO_TIME,
            )

        # 7. Rule 5: 检测并列最新（updated_at 唯一性收敛）
        if chosen.updated_at is not None:
            tied = [c for c in working if c.updated_at == chosen.updated_at]
            if len(tied) > 1:
                return NeedClarification(
                    candidates=tuple(tied),
                    reason=REASON_AMBIGUOUS_AFTER_FILTERS,
                )

        return ResolvedMemory(candidate=chosen, reason=REASON_LATEST_UPDATED)

    @staticmethod
    def _select_latest(candidates: list[MemoryCandidate]) -> MemoryCandidate | None:
        """从候选中按 updated_at DESC 取最新一条。

        行为：
          - 全部 updated_at 为 None → 返回 None（让 caller 走 NeedClarification）；
          - 否则取 updated_at 最大值对应的第一条（按 list 顺序去重第二条以避免
            "并列最新"被悄悄收敛到第一条 —— 此情况由 caller 进一步判断）。
        """
        # 防御性去重：candidates 可能含重复；按 (updated_at, created_at, task_type)
        # 复合键去重以保证稳定顺序。
        seen: set[tuple] = set()
        unique: list[MemoryCandidate] = []
        for c in candidates:
            key = (c.updated_at, c.created_at, c.task_type)
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        if not unique:
            return None
        # 过滤掉 updated_at 为 None 的；若全部 None 则返回 None
        with_time = [c for c in unique if c.updated_at is not None]
        if not with_time:
            return None
        # updated_at 最大 → 取第一条
        with_time.sort(key=lambda c: c.updated_at, reverse=True)  # type: ignore[arg-type,return-value]
        return with_time[0]


__all__ = [
    'MemoryTaskResolutionPolicy',
    'REASON_UNIQUE_CANDIDATE',
    'REASON_SCOPE_MATCHED_UNIQUE',
    'REASON_HINT_MATCHED_UNIQUE',
    'REASON_PENDING_CONFIRMATION',
    'REASON_LATEST_UPDATED',
    'REASON_AMBIGUOUS_AFTER_FILTERS',
    'REASON_AMBIGUOUS_NO_TIME',
    'REASON_NO_ACTIVE_CANDIDATE',
]