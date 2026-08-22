"""memory_capability.py —— Memory Capability 数据契约（P1-B）

P1-A 把 taskType 集合从 Schema 闭集移到 ``MemoryTaskTypePolicy`` 显式注册，
调用方通过 ``MemoryTaskTypePolicy.create_for(extra_task_types=..., ...)``
拼装业务扩展。

P1-B 目标：让"业务声明自己的 Memory 能力"而不是"调用方为业务拼 Memory 配置"。

  Business Module  →  MemoryCapability  →  MemoryCapabilityRegistry  →  MemoryTaskTypePolicy

换言之：

  Memory Core 不再 import 业务模块；
  业务模块只声明 ``MemoryCapability`` 元数据；
  Memory Core 仅消费 Registry（数据形态）。

本文件只定义最底层的数据契约 ``MemoryCapability`` —— frozen、不可变、
不依赖任何业务 / Tool / Agent / LangGraph 模块。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemoryCapability(BaseModel):
    """单个业务 Workflow 的 Memory 接入元数据。

    字段语义：
      task_type          —— 该业务在 Memory 写入链路上的 task_type 字面；
                            必须在整个 Registry 内唯一（构造时校验）。
      eligible_tools     —— 该业务的"Memory-eligible tool"集合；
                            触发 Policy 命中这些 tool 的成功调用即触发 Memory 写入；
                            同一 tool 不允许在多个 capability 中重复注册。
                            可以为空 frozenset（表示"该 capability 不绑定任何
                            Memory-eligible tool"，例如 GENERIC 兜底类型）。
      description        —— 人类可读说明（用于审计 / 调试日志）；不影响写入逻辑。
      default_task_type  —— 可选；当 proposal.task_type 缺省时使用的兜底 task_type；
                            默认 = task_type 自身（保持单 capability 自洽）。

    不变式：
      - extra='forbid'：禁止保存任何业务数据字段（金额 / 员工 / 审批状态等）；
      - frozen=True：构造后不可变（避免运行期漂移破坏可审计性）；
      - task_type 非空字符串（构造时校验）；
      - eligible_tools 项必须为非空字符串（若非空集合）；
      - default_task_type ∈ {task_type}（兜底只能回到自身）。

    边界：
      - 本契约不携带 user_id / tenant_id / conversation_id / 权限 / 业务字段；
      - 不 import 任何业务 / Tool / Agent / LangGraph 模块；
      - 仅承载"Memory 接入元数据"。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    task_type: str = Field(min_length=1)
    eligible_tools: frozenset[str] = Field(default_factory=frozenset)
    description: str = ''
    default_task_type: str = ''

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        # task_type 非空 + 大小写 / 空白校验：避免 LLM 写入端出现大小写漂移。
        if not self.task_type or not self.task_type.strip():
            raise ValueError('MemoryCapability.task_type 必须为非空字符串')
        if self.task_type != self.task_type.strip():
            raise ValueError(
                f'MemoryCapability.task_type={self.task_type!r} 不允许首尾空白'
            )

        # eligible_tools 项校验（允许空集合，但非空集合中每项必须为非空字符串）
        for tool in self.eligible_tools:
            if not isinstance(tool, str) or not tool:
                raise ValueError(
                    f'MemoryCapability.eligible_tools 项必须为非空字符串，得到 {tool!r}'
                )

        # default_task_type 兜底校验：必须 ∈ {task_type}，否则视为配置错误。
        default = self.default_task_type or self.task_type
        if default != self.task_type:
            raise ValueError(
                f'MemoryCapability.default_task_type={default!r} '
                f'必须等于 task_type={self.task_type!r}'
            )

    def resolved_default_task_type(self) -> str:
        """返回兜底 task_type；未设置时使用 task_type 自身。"""
        return self.default_task_type or self.task_type


__all__ = ['MemoryCapability']