"""memory_metrics.py —— Memory Write Path 领域指标聚合层（Phase 5C）

职责：
    在纯 Python 内存里对 ``MemoryAuditEvent`` 流做聚合，输出结构化的
    ``MemoryMetricsSnapshot``。用于离线 / 评估 / 本地诊断场景。

约束：
    - **不**接 Prometheus / 数据库 / Kafka / LangSmith / RuntimeHook；
    - **不**修改 ``MemoryAuditEvent``，仅读取字段做累加；
    - **不**保存任何敏感字段（Privacy boundary 已在 ``memory_audit.py``
      层通过 ``extra='forbid'`` 强制，本层只搬运受信任的元数据字段）；
    - snapshot 是不可变 Pydantic 模型，便于跨线程 / 跨请求安全传递。

行为：
    - ``record_event`` 单调累加计数器；
    - ``snapshot`` 返回确定性、按 key 排序的 dict；
    - ``reset`` 清空所有计数。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.memory.memory_audit import MemoryAuditEvent


class MemoryMetricsSnapshot(BaseModel):
    """Memory 领域指标的不可变快照。

    所有计数字段 ``ge=0``，杜绝负数；``extra='forbid'`` 防止意外字段。
    ``*_counts`` 字段在 snapshot 时按 key 升序排列，保证 deterministic。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    trigger_total: int = Field(default=0, ge=0)
    write_attempt_total: int = Field(default=0, ge=0)
    write_success_total: int = Field(default=0, ge=0)
    write_failure_total: int = Field(default=0, ge=0)
    trigger_reason_counts: dict[str, int] = Field(default_factory=dict)
    action_counts: dict[str, int] = Field(default_factory=dict)
    error_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator(
        'trigger_reason_counts', 'action_counts', 'error_counts',
    )
    @classmethod
    def _non_negative_counts(
        cls, value: dict[str, int],
    ) -> dict[str, int]:
        for key, count in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f'counts key 必须是非空字符串，得到 {key!r}',
                )
            if not isinstance(count, int) or isinstance(count, bool):
                raise ValueError(
                    f'counts[{key!r}] 必须是 int，得到 {type(count).__name__}',
                )
            if count < 0:
                raise ValueError(
                    f'counts[{key!r}] 不允许负数，得到 {count}',
                )
        return value

    @property
    def success_ratio(self) -> float | None:
        """``write_success_total / write_attempt_total``；无尝试时返回 ``None``。"""
        if self.write_attempt_total == 0:
            return None
        return self.write_success_total / self.write_attempt_total


class MemoryMetricsCollector:
    """Memory Audit Event 的内存指标聚合器。

    非线程安全 —— 与 ``LoggingAuditRecorder`` 假设一致（Hook 同步调用）。
    """

    def __init__(self) -> None:
        self._trigger_total = 0
        self._write_attempt_total = 0
        self._write_success_total = 0
        self._write_failure_total = 0
        self._trigger_reason_counts: dict[str, int] = defaultdict(int)
        self._action_counts: dict[str, int] = defaultdict(int)
        self._error_counts: dict[str, int] = defaultdict(int)
        self._event_total = 0

    @property
    def event_total(self) -> int:
        """已累计的 event 总数（含未触发 / 无写动作的 event）。"""
        return self._event_total

    def record_event(self, event: MemoryAuditEvent) -> None:
        """聚合单个 ``MemoryAuditEvent``。不修改入参，不保存敏感字段。"""
        if not isinstance(event, MemoryAuditEvent):
            raise TypeError(
                'MemoryMetricsCollector.record_event 需要 MemoryAuditEvent 输入，'
                f'得到 {type(event).__name__}',
            )
        self._event_total += 1

        if event.triggered:
            self._trigger_total += 1
            self._bump(event.trigger_reason or '', self._trigger_reason_counts)

        # action 分布：聚合所有出现过的 proposal_action 字符串（包括 NONE），
        # 便于观察"为什么 Pipeline 输出了 NONE / UPSERT / ABANDON / COMPLETE"。
        if event.proposal_action:
            self._bump(event.proposal_action, self._action_counts)

        if event.write_attempted:
            self._write_attempt_total += 1
            if event.write_success:
                self._write_success_total += 1
            else:
                self._write_failure_total += 1
            if event.error_type:
                self._bump(event.error_type, self._error_counts)
        elif event.error_type:
            # 即使未尝试写（例如 Pipeline 错误），error_type 仍然要聚合，
            # 与 ``_harm_detected`` 关心的"曾经发生过错误"语义保持一致。
            self._bump(event.error_type, self._error_counts)

    def snapshot(self) -> MemoryMetricsSnapshot:
        """返回不可变快照，``*_counts`` 按 key 升序保证 deterministic。"""
        return MemoryMetricsSnapshot(
            trigger_total=self._trigger_total,
            write_attempt_total=self._write_attempt_total,
            write_success_total=self._write_success_total,
            write_failure_total=self._write_failure_total,
            trigger_reason_counts=_sorted_counts(self._trigger_reason_counts),
            action_counts=_sorted_counts(self._action_counts),
            error_counts=_sorted_counts(self._error_counts),
        )

    def reset(self) -> None:
        """清空所有计数。"""
        self._trigger_total = 0
        self._write_attempt_total = 0
        self._write_success_total = 0
        self._write_failure_total = 0
        self._trigger_reason_counts.clear()
        self._action_counts.clear()
        self._error_counts.clear()
        self._event_total = 0

    @staticmethod
    def _bump(key: str, target: dict[str, int]) -> None:
        if key == '':
            return
        target[key] += 1


def _sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    """按 key 升序返回新 dict，保证 snapshot 字典序稳定。"""
    return {key: counts[key] for key in sorted(counts)}


def aggregate_events(events: list[MemoryAuditEvent]) -> MemoryMetricsSnapshot:
    """便捷函数：对一组 event 一次性聚合并返回 snapshot。

    不在内部维护 collector 状态；适合测试 / 一次性分析。
    """
    collector = MemoryMetricsCollector()
    for event in events:
        collector.record_event(event)
    return collector.snapshot()


__all__ = [
    'MemoryMetricsCollector',
    'MemoryMetricsSnapshot',
    'aggregate_events',
]
