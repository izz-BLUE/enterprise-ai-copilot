"""memory_candidate.py —— Memory Candidate / Resolution 结果数据契约（P1-C）

P1-C 解决"多个 ACTIVE Memory 同时存在时如何确定恢复哪个"的确定性恢复问题。

数据契约：
  MemoryCandidate     —— 一条待恢复 memory 的元数据视图（不携带身份字段）；
  ResolvedMemory      —— Resolution Policy 收敛出的唯一结果；
  NeedClarification   —— 多个候选项无法确定时返回的澄清信号；
  ResolutionDecision  —— ResolvedMemory | NeedClarification 联合返回结果（带 reason）。

边界纪律：
  * MemoryCandidate 不携带 user_id / tenant_id / permission / role / token / jwt 等
    身份字段 —— 身份已经在 Java (VerifiedIdentity) 边界解析完成，Python 侧不重复判定。
  * pending_step 是从 task_state 内派生出的"等待步骤"语义标记（如 'confirmation'），
    调用方在构造候选时自行从 task_state.get('pending_step') 提取；
    Resolution Policy 不触碰 task_state 内部 schema。
  * conversation_id 是 Java → Python 通信用的 scope 标识，与 user_id 复合构成
    (user_id, conversation_id) 复合 key（已由 Java 侧处理）；
    Python 侧在 Resolution 中仅作为 in-scope filter 使用，不做身份判定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field


# 允许的状态枚举（与 Java 侧 AiTaskMemoryService.TaskStatus 对齐）。
MemoryCandidateStatus = str  # ACTIVE / COMPLETED / ABANDONED；Resolution 只用 ACTIVE


class MemoryCandidate(BaseModel):
    """一条待恢复 Memory 的元数据视图。

    字段语义：
      conversation_id  —— Java 端 scope 标识；用于 in-scope filter（不参与权限判定）。
      task_type        —— MemoryTaskType（GENERIC / LEAVE_REQUEST / BUSINESS_ACTION /
                         或通过 MemoryTaskTypePolicy / MemoryCapabilityRegistry 注册的
                         业务类别，如 EXPENSE_REQUEST）。
      status           —— 与 Java 侧 TaskStatus 对齐；Resolution 仅考虑 ACTIVE 候选。
      task_state       —— Context Snapshot（dict[str, Any] | None）；只承载"任务当前进度"
                         等续接事实，不包含 user_id / 权限 / 业务审批字段
                         （由 MemoryWritePolicy._scrub_task_state 兜底）。
      summary          —— 任务摘要；可空（≤ 500 chars；上限由 Java 侧 enforce）。
      created_at       —— 候选创建时间（ISO 8601）；Resolution Policy 用于排序。
      updated_at       —— 候选最近更新时间（ISO 8601）；Resolution Policy 用于排序。
      pending_step     —— 从 task_state 派生的"等待步骤"语义标记（如 'confirmation'）；
                         用于 Rule 3 的优先选择；调用方在构造候选时填入。

    不变式：
      - extra='forbid'：禁止 user_id / tenant_id / role / permission / token / 等身份字段；
      - frozen=True：构造后不可变；
      - updated_at >= created_at（如果两者都提供）。
      - status='ACTIVE'：Resolution Policy 实际只考虑 ACTIVE 候选；其它 status 视为不可恢复。

    不属于本契约的内容（不应注入）：
      - 用户身份（user_id / employee_id / conversation_id 之外的身份键）；
      - 权限判定字段（role / permission / allow_* / business_date）；
      - 业务审批 / 业务执行结果（approval_status / expense_amount / leave_balance）；
      - 凭据（token / jwt / password）。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    conversation_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    status: MemoryCandidateStatus = 'ACTIVE'
    task_state: dict[str, Any] | None = None
    summary: str = ''
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pending_step: str | None = None

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        # 时间顺序校验：updated_at 应 ≥ created_at
        if (
            self.created_at is not None
            and self.updated_at is not None
            and self.updated_at < self.created_at
        ):
            raise ValueError(
                f'MemoryCandidate.updated_at ({self.updated_at.isoformat()}) '
                f'必须 ≥ created_at ({self.created_at.isoformat()})'
            )
        # task_state forbidden keys 兜底（防御性）：若调用方不慎注入身份字段，
        # 让构造直接失败（fail-loud），避免悄悄绕过 Identity Boundary。
        if self.task_state:
            _FORBIDDEN_TASK_STATE_KEYS = {
                'userId', 'user_id',
                'employeeId', 'employee_id',
                'tenantId', 'tenant_id',
                'role', 'permission',
                'allowEval', 'allow_eval',
                'allowBusinessActions', 'allow_business_actions',
                'businessDate', 'business_date',
                'traceId', 'trace_id',
                'token', 'nonce',
                'idempotencyKey', 'idempotency_key',
                'jwt', 'password',
            }
            for forbidden in _FORBIDDEN_TASK_STATE_KEYS:
                if forbidden in self.task_state:
                    raise ValueError(
                        f'MemoryCandidate.task_state 包含禁止键 {forbidden!r}；'
                        'Memory Candidate 是 Context Snapshot，不承载身份字段'
                    )

    def is_recoverable(self) -> bool:
        """是否可恢复候选（ACTIVE 状态）。其它状态（COMPLETED / ABANDONED）视为不可恢复。"""
        return self.status == 'ACTIVE'


# ===========================================================================
# Resolution 结果
# ===========================================================================


class ResolvedMemory(BaseModel):
    """Resolution Policy 收敛出的唯一恢复目标。"""

    model_config = ConfigDict(extra='forbid', frozen=True)

    candidate: MemoryCandidate
    reason: str = ''  # debug 用：例如 'latest_updated' / 'pending_confirmation'

    @property
    def task_type(self) -> str:
        return self.candidate.task_type


class NeedClarification(BaseModel):
    """Resolution Policy 无法确定唯一目标时返回的澄清信号。

    字段语义：
      candidates —— 候选列表（按规则顺序收敛的"等优先级"候选）；
      reason     —— debug 用字符串，例如 'ambiguous_after_filters'。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    candidates: tuple[MemoryCandidate, ...]
    reason: str = ''

    def candidate_task_types(self) -> tuple[str, ...]:
        return tuple(c.task_type for c in self.candidates)


class ResolutionEmpty(BaseModel):
    """无可恢复候选（候选列表为空或全部非 ACTIVE）。"""

    model_config = ConfigDict(extra='forbid', frozen=True)

    reason: str = 'no_active_candidate'


# 联合返回类型
ResolutionDecision = Union[ResolvedMemory, NeedClarification, ResolutionEmpty]


__all__ = [
    'MemoryCandidate',
    'MemoryCandidateStatus',
    'NeedClarification',
    'ResolutionDecision',
    'ResolutionEmpty',
    'ResolvedMemory',
]