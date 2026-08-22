"""Scoped Conversation Memory Phase 5F Release Gate 评估器。

输入（全部 Pydantic 严格白名单，不含敏感字段）：
    - ``MemoryMetricsSnapshot``     —— Phase 5C 指标聚合
    - ``MemoryEvaluationSummary``   —— Phase 5A 评估结果聚合
    - ``MemoryCostSummary``         —— Phase 5D 成本结果聚合
    - ``MemoryGuardRailStatus``     —— Phase 5E Guard Rail 状态

输出：``MemoryReleaseAuditResult`` —— 5 项 pass + blockers + 推荐。
判定规则（fail-closed）：
    - 全部 pass ⇒ ``enabled_recommendation = 'READY'``
    - 任一失败 ⇒ ``BLOCKED``，并按固定顺序写出 ``blockers`` 字符串

阈值（P0 默认值，调用方可通过 ``thresholds`` 覆盖）：
    - ``min_mean_score``        = 0.8
    - ``max_mean_overhead_ms``  = 500.0
    - 任意 case 评估 ``harm_total > 0`` ⇒ isolation 直接不通过

约束：
    - 不接数据库 / Java / RuntimeHook / 配置中心；
    - 不修改 MemoryPipeline / MemoryRuntimeHook；
    - 不复制任何 user_id / employee_id / conversation_id / summary / task_state
      （输入层不携带这些字段，evaluator 也不从外部获取）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.memory.memory_metrics import MemoryMetricsSnapshot
from eval.memory.memory_release_audit import (
    MemoryCostSummary,
    MemoryEvaluationSummary,
    MemoryGuardRailStatus,
    MemoryReleaseAuditResult,
)


@dataclass(frozen=True)
class ReleaseThresholds:
    """Release Gate 阈值（P0 默认值，可由调用方覆盖）。"""

    min_mean_score: float = 0.8
    max_mean_overhead_ms: float = 500.0


class MemoryReleaseEvaluator:
    """对离线 Release Gate 输入做确定性判定。"""

    def __init__(self, thresholds: ReleaseThresholds | None = None) -> None:
        self._thresholds = thresholds or ReleaseThresholds()

    def evaluate(
        self,
        *,
        metrics: MemoryMetricsSnapshot,
        evaluation: MemoryEvaluationSummary,
        cost: MemoryCostSummary,
        guard_rail: MemoryGuardRailStatus,
    ) -> MemoryReleaseAuditResult:
        safety_pass, safety_blockers = self._check_safety(metrics, guard_rail)
        rollout_pass, rollout_blockers = self._check_rollout(guard_rail)
        isolation_pass, isolation_blockers = self._check_isolation(evaluation)
        evaluation_pass, evaluation_blockers = self._check_evaluation(evaluation)
        cost_pass, cost_blockers = self._check_cost(cost)

        blockers: list[str] = []
        blockers.extend(safety_blockers)
        blockers.extend(rollout_blockers)
        blockers.extend(isolation_blockers)
        blockers.extend(evaluation_blockers)
        blockers.extend(cost_blockers)

        ready = (
            safety_pass
            and rollout_pass
            and isolation_pass
            and evaluation_pass
            and cost_pass
        )

        return MemoryReleaseAuditResult(
            safety_pass=safety_pass,
            rollout_pass=rollout_pass,
            isolation_pass=isolation_pass,
            evaluation_pass=evaluation_pass,
            cost_pass=cost_pass,
            enabled_recommendation='READY' if ready else 'BLOCKED',
            blockers=blockers,
        )

    # --- 各维度判定 -----------------------------------------------------

    def _check_safety(
        self,
        metrics: MemoryMetricsSnapshot,
        guard_rail: MemoryGuardRailStatus,
    ) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if not guard_rail.safety_ok:
            blockers.append('safety: guard rail safety_ok is False')
        if metrics.write_failure_total > 0:
            blockers.append(
                f'safety: write_failure_total={metrics.write_failure_total} > 0',
            )
        # 任何 error_type 计数 > 0 都需要人工 review —— P0 阶段直接 fail-closed。
        if any(count > 0 for count in metrics.error_counts.values()):
            blockers.append('safety: error_counts contains non-zero entries')
        return (not blockers, blockers)

    def _check_rollout(
        self, guard_rail: MemoryGuardRailStatus,
    ) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if not guard_rail.rollout_enabled:
            blockers.append('rollout: rollout_enabled is False')
        if guard_rail.rollout_percentage <= 0:
            blockers.append('rollout: rollout_percentage <= 0')
        if not guard_rail.quota_ok:
            blockers.append('rollout: quota_ok is False')
        return (not blockers, blockers)

    def _check_isolation(
        self, evaluation: MemoryEvaluationSummary,
    ) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if evaluation.harm_total > 0:
            blockers.append(
                f'isolation: harm_total={evaluation.harm_total} > 0 '
                '(cross-user/conversation contamination detected)',
            )
        return (not blockers, blockers)

    def _check_evaluation(
        self, evaluation: MemoryEvaluationSummary,
    ) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if evaluation.case_total <= 0:
            blockers.append('evaluation: case_total == 0 (no cases evaluated)')
        if evaluation.passed_total < evaluation.case_total:
            blockers.append(
                f'evaluation: passed_total={evaluation.passed_total} '
                f'< case_total={evaluation.case_total}',
            )
        if evaluation.mean_score < self._thresholds.min_mean_score:
            blockers.append(
                f'evaluation: mean_score={evaluation.mean_score} '
                f'< min_mean_score={self._thresholds.min_mean_score}',
            )
        return (not blockers, blockers)

    def _check_cost(
        self, cost: MemoryCostSummary,
    ) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if cost.sample_total <= 0:
            blockers.append('cost: sample_total == 0 (no cost samples)')
        if cost.mean_latency_overhead > self._thresholds.max_mean_overhead_ms:
            blockers.append(
                f'cost: mean_latency_overhead={cost.mean_latency_overhead}ms '
                f'> max_mean_overhead_ms={self._thresholds.max_mean_overhead_ms}ms',
            )
        if cost.sample_total > 0 and cost.positive_roi_total == 0:
            # 只有"全部 ROI ≤ 0"才算失败；个别 ROI ≤ 0 是可接受的。
            blockers.append(
                f'cost: positive_roi_total={cost.positive_roi_total} '
                f'== 0 (all samples have non-positive ROI)',
            )
        return (not blockers, blockers)


__all__ = ['MemoryReleaseEvaluator', 'ReleaseThresholds']
