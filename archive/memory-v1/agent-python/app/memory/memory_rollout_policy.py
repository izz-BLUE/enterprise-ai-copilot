"""memory_rollout_policy.py —— Memory ENABLED 生产灰度策略（Phase 5E）

职责：
    在不修改 MemoryPipeline / MemoryRuntimeHook 核心逻辑的前提下，
    给生产环境提供"按 subject_id 灰度开启 Memory"的能力。

行为：
    - ``enabled=False`` → 一律不开启（紧急熔断）；
    - ``enabled=True`` 且 ``percentage=0`` → 一律不开启；
    - ``enabled=True`` 且 ``percentage=100`` → 一律开启；
    - 中间百分比使用 ``sha256(subject_id)`` 取模 100：
        ``bucket < percentage`` ⇒ 开启；否则不开启；
      同一个 ``subject_id`` 永远落到同一个 bucket，禁止随机。

约束：
    - 不接配置中心 / 数据库 / Redis / Java；
    - 不读 ``MemoryAuditEvent`` / 不写 Runtime 状态；
    - ``extra='forbid'`` 与 ``percentage`` 范围由 Pydantic 兜底。
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field


class MemoryRolloutPolicy(BaseModel):
    """Memory 灰度发布策略。

    ``enabled`` 是总开关；``percentage`` 是灰度比例（0-100）。
    """

    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    percentage: int = Field(default=0, ge=0, le=100)

    def should_enable(self, subject_id: str) -> bool:
        """判断给定 subject_id 是否应启用 Memory 路径。

        返回 ``False`` 的所有场景（安全默认）：
            - ``enabled=False``；
            - ``percentage=0``；
            - ``subject_id`` 为空字符串 / 非字符串；
            - hash bucket 未命中灰度区间。
        """
        if not self.enabled:
            return False
        if not isinstance(subject_id, str) or subject_id == '':
            return False
        if self.percentage == 0:
            return False
        if self.percentage == 100:
            return True
        bucket = _bucket(subject_id)
        return bucket < self.percentage


def _bucket(subject_id: str) -> int:
    """计算 subject_id 在 0-99 灰度桶中的稳定位置。"""
    digest = hashlib.sha256(subject_id.encode('utf-8')).digest()
    # 取前 8 字节作为无符号整数，与 100 取模得到稳定桶号。
    value = int.from_bytes(digest[:8], byteorder='big', signed=False)
    return value % 100


__all__ = ['MemoryRolloutPolicy']
