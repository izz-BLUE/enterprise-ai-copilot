"""Scoped Conversation Memory Phase 5F Release Audit 数据契约。

本模块只描述"Release Gate 输出形状"，不参与 Memory Runtime。
``MemoryReleaseAuditResult`` 字段集合在 eval 层是封闭的：
任何额外字段（包含敏感业务字段）都会被 Pydantic 拒绝。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryEvaluationSummary(BaseModel):
    """Phase 5A 离线评估结果聚合。

    字段：
      - ``case_total``     —— 评估过的 case 总数；
      - ``passed_total``   —— 评估通过的 case 数；
      - ``harm_total``     —— 被判定为有害的 case 数；
      - ``mean_score``     —— 评估分均值（0.0-1.0）。

    约束：``extra='forbid'``；不允许携带任何 user_id / employee_id 等字段。
    """

    model_config = ConfigDict(extra='forbid')

    case_total: int = Field(ge=0)
    passed_total: int = Field(ge=0)
    harm_total: int = Field(ge=0)
    mean_score: float = Field(ge=0.0, le=1.0)


class MemoryCostSummary(BaseModel):
    """Phase 5D 成本 / 延迟评估结果聚合。

    字段：
      - ``sample_total``          —— 成本样本数；
      - ``positive_roi_total``    —— ``roi_score > 0`` 的样本数；
      - ``mean_latency_overhead`` —— 平均 latency overhead（毫秒）。

    约束：``extra='forbid'``。
    """

    model_config = ConfigDict(extra='forbid')

    sample_total: int = Field(ge=0)
    positive_roi_total: int = Field(ge=0)
    mean_latency_overhead: float = Field(ge=0.0)


class MemoryGuardRailStatus(BaseModel):
    """Phase 5E Guard Rail 当前状态。

    字段：
      - ``rollout_enabled``   —— Rollout Policy 主开关；
      - ``rollout_percentage``—— 灰度比例（0-100）；
      - ``quota_ok``          —— 配额策略白名单当前可用；
      - ``safety_ok``         —— 离线 safety 扫描未发现 harm。

    约束：``extra='forbid'``。
    """

    model_config = ConfigDict(extra='forbid')

    rollout_enabled: bool
    rollout_percentage: int = Field(ge=0, le=100)
    quota_ok: bool
    safety_ok: bool


class MemoryReleaseAuditResult(BaseModel):
    """Memory Write Path 终态发布门禁结果。

    字段：
      - 五项独立 pass flag（safety / rollout / isolation / evaluation / cost）；
      - ``enabled_recommendation`` —— 终态结论：
          * ``READY``   ：五项全部通过；
          * ``BLOCKED``：任一项不通过；
      - ``blockers`` —— 不通过项的可读原因列表（确定性顺序）。

    约束：
      - ``extra='forbid'`` 防止任何额外字段进入；
      - ``blockers`` 不可携带 user_id / employee_id / conversation_id /
        summary / task_state 等敏感字段（输入层根本不存在这些字段）。
    """

    model_config = ConfigDict(extra='forbid')

    safety_pass: bool
    rollout_pass: bool
    isolation_pass: bool
    evaluation_pass: bool
    cost_pass: bool
    enabled_recommendation: Literal['READY', 'BLOCKED']
    blockers: list[str] = Field(default_factory=list)


__all__ = [
    'MemoryCostSummary',
    'MemoryEvaluationSummary',
    'MemoryGuardRailStatus',
    'MemoryReleaseAuditResult',
]
