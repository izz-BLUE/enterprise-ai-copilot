"""Scoped Conversation Memory Phase 5D 离线成本 / 延迟评估器。

输入：``MemoryCostSample`` —— 离线的延迟 / token / 成功标志观察记录，
不是 AgentState 或 MemoryPipeline 状态。

输出：``MemoryCostResult`` —— 派生指标：

* ``latency_overhead_ms``      —— ``memory_latency_ms - baseline_latency_ms``；
* ``latency_overhead_ratio``   —— ``overhead / baseline``；基线为 0 时返回 ``None``；
* ``token_total``              —— 三个 token 维度之和；
* ``recovery_improvement``     —— 真值表（见 ``_recovery_improvement``）；
* ``roi_score``                —— 简单线性 ROI：
  ``recovery_improvement - latency_overhead_ms/1000 - token_total/10000``；
  当 ``recovery_improvement is None`` 时返回 ``None``。

约束：
  - 不调用 LLM / Java / 数据库 / MemoryPipeline；
  - 不修改 ``MemoryCostSample``，所有计算为纯函数；
  - 输出 ``extra='forbid'``，结果只含派生指标。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from eval.memory.memory_cost_schema import MemoryCostSample


class MemoryCostResult(BaseModel):
    """单个 Memory 成本样本的离线派生指标。"""

    model_config = ConfigDict(extra='forbid')

    case_id: str
    latency_overhead_ms: float
    latency_overhead_ratio: float | None
    token_total: int
    recovery_improvement: int | None
    roi_score: float | None


def _recovery_improvement(
    without: bool | None,
    with_memory: bool | None,
) -> int | None:
    if without is None or with_memory is None:
        return None
    if without is True and with_memory is True:
        return 0
    if without is False and with_memory is True:
        return 1
    if without is True and with_memory is False:
        return -1
    # without is False and with_memory is False
    return 0


def _roi(
    recovery: int | None,
    latency_overhead_ms: float,
    token_total: int,
) -> float | None:
    if recovery is None:
        return None
    return round(
        recovery - (latency_overhead_ms / 1000.0) - (token_total / 10000.0),
        6,
    )


def _latency_overhead_ratio(
    overhead_ms: float,
    baseline_ms: float,
) -> float | None:
    if baseline_ms == 0:
        return None
    return round(overhead_ms / baseline_ms, 6)


class MemoryCostEvaluator:
    """对离线 ``MemoryCostSample`` 派生确定性成本指标。"""

    def evaluate(self, sample: MemoryCostSample) -> MemoryCostResult:
        if not isinstance(sample, MemoryCostSample):
            raise TypeError(
                'MemoryCostEvaluator.evaluate 需要 MemoryCostSample 输入，'
                f'得到 {type(sample).__name__}',
            )

        latency_overhead_ms = round(
            sample.memory_latency_ms - sample.baseline_latency_ms,
            6,
        )
        token_total = (
            sample.extractor_input_tokens
            + sample.extractor_output_tokens
            + sample.planner_memory_tokens
        )
        recovery = _recovery_improvement(
            sample.success_without_memory,
            sample.success_with_memory,
        )
        ratio = _latency_overhead_ratio(
            latency_overhead_ms,
            sample.baseline_latency_ms,
        )
        roi = _roi(recovery, latency_overhead_ms, token_total)

        return MemoryCostResult(
            case_id=sample.case_id,
            latency_overhead_ms=latency_overhead_ms,
            latency_overhead_ratio=ratio,
            token_total=token_total,
            recovery_improvement=recovery,
            roi_score=roi,
        )


__all__ = ['MemoryCostEvaluator', 'MemoryCostResult']
