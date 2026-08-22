"""Scoped Conversation Memory Phase 5D 离线成本评估契约。

本模块只描述"成本 / 延迟"评估的输入样本（``MemoryCostSample``），
不参与 Memory Runtime。``MemoryCostResult`` 由
``memory_cost_evaluator.MemoryCostEvaluator`` 派生并返回。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemoryCostSample(BaseModel):
    """单个 Memory 场景的成本 / 延迟观察样本。

    字段：
      case_id                  —— 关联的离线 case id，仅用于结果定位；
      baseline_latency_ms      —— 关闭 Memory 路径时的端到端延迟（毫秒）；
      memory_latency_ms        —— 开启 Memory 路径时的端到端延迟（毫秒）；
      extractor_input_tokens   —— Memory Extractor 输入侧 token 数；
      extractor_output_tokens  —— Memory Extractor 输出侧 token 数；
      planner_memory_tokens    —— Planner 提示中由 Memory 注入的 token 数；
      success_without_memory   —— 关闭 Memory 时任务是否成功（None 表示未知）；
      success_with_memory      —— 开启 Memory 时任务是否成功（None 表示未知）。

    约束：
      - 所有数值字段 ``ge=0``；
      - ``extra='forbid'`` —— 任何未声明字段直接 ``ValidationError``；
      - 成功标志允许为 ``None``，便于在缺少对照观察时仍能完成延迟 / token
        维度的离线评估。
    """

    model_config = ConfigDict(extra='forbid')

    case_id: str = Field(min_length=1)
    baseline_latency_ms: float = Field(ge=0.0)
    memory_latency_ms: float = Field(ge=0.0)
    extractor_input_tokens: int = Field(ge=0)
    extractor_output_tokens: int = Field(ge=0)
    planner_memory_tokens: int = Field(ge=0)
    success_without_memory: bool | None = None
    success_with_memory: bool | None = None


__all__ = ['MemoryCostSample']
