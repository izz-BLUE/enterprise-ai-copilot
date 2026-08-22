"""memory_quota_policy.py —— Memory 写入配额 / 状态机保护（Phase 5E）

职责：
    在 ``MemoryWritePolicy`` 通过后，给"写入"动作再叠一层保守的
    状态机保护，避免 P0 阶段误把 NONE 提案落库、或覆盖已经
    COMPLETED / ABANDONED 的任务。

行为（白名单）：
    - ``existing_status = ACTIVE``  + ``UPSERT``  → 允许（任务续期覆盖）；
    - ``existing_status = COMPLETED`` + ``COMPLETE`` → 允许（幂等）；
    - ``existing_status = ABANDONED`` + ``ABANDON`` → 允许（幂等）；
    - ``existing_status = None`` + ``UPSERT``      → 允许（首条创建）；
    - 其余组合一律拒绝（保守默认，fail-closed）。

约束：
    - 不读 ``MemoryAuditEvent``、不查数据库 / Redis / Java；
    - 仅依赖 ``existing_status`` 与 ``command_action`` 两个字符串字面量。
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict


# 与 ``app/schemas/memory_schema.py`` 中的枚举保持一致；这里再声明一次
# 避免 P0 阶段对 schema 的反向 import 引入额外耦合。
_ALLOWED_EXISTING_STATUS: Final = frozenset({'ACTIVE'})
_ALLOWED_TERMINAL_STATUS: Final = frozenset({'COMPLETED', 'ABANDONED'})
_ALLOWED_TERMINAL_ACTIONS: Final = frozenset({'COMPLETE', 'ABANDON'})
_ALLOWED_NEW_ACTIONS: Final = frozenset({'UPSERT'})


class MemoryQuotaPolicy(BaseModel):
    """Memory 写入配额策略（P0 状态机白名单）。"""

    model_config = ConfigDict(extra='forbid')

    def allow_write(
        self,
        existing_status: str | None,
        command_action: str | None,
    ) -> bool:
        """判断给定的 (existing_status, command_action) 是否允许写入。

        ``existing_status`` 是当前 Memory 任务的状态；``None`` 表示没有
        历史记录。``command_action`` 是从 ``MemoryProposal.action`` 派生
        出的写命令动作。

        返回 ``True`` 的全部场景：
            - (ACTIVE, UPSERT)
            - (COMPLETED, COMPLETE)
            - (ABANDONED, ABANDON)
            - (None, UPSERT)
        """
        if command_action is None or command_action == 'NONE':
            return False

        if existing_status is None:
            return command_action in _ALLOWED_NEW_ACTIONS

        if existing_status in _ALLOWED_EXISTING_STATUS:
            return command_action == 'UPSERT'

        if existing_status in _ALLOWED_TERMINAL_STATUS:
            return (
                existing_status == 'COMPLETED'
                and command_action == 'COMPLETE'
            ) or (
                existing_status == 'ABANDONED'
                and command_action == 'ABANDON'
            )

        return False


__all__ = ['MemoryQuotaPolicy']
