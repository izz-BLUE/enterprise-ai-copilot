"""memory_capability_registry.py —— Memory Capability Registry（P1-B）

P1-B 引入 Workflow Capability Registration Boundary。

  Business Module
        |
        |  声明 MemoryCapability（纯元数据）
        v
  MemoryCapabilityRegistry
        |
        |  提供 task_types / eligible_tools / tool_mapping
        v
  MemoryTaskTypePolicy
        |
        v
  Memory Core

边界纪律：

  * 本模块不 import 任何业务 / Tool / Agent / LangGraph / Database / HTTP 模块；
  * Registry 仅承载"Memory 接入元数据"（task_type / eligible_tools / description）；
  * 不保存 user_id / tenant_id / conversation_id / 权限 / 业务数据。

不实现：

  * Plugin System / Service Discovery / Remote Registry / DB Registry /
    Dynamic Runtime Loading —— 本 Registry 是显式构造的纯 Python 数据结构，
    业务方通过 ``register(capability)`` 显式登记，禁止隐式发现。

可审计：

  * ``register`` 只能通过构造时传入 ``capabilities`` 参数；
  * 构造后不可变（frozen=True）；
  * ``describe()`` 输出"当前注册了什么"的可读快照，便于审计。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.memory_capability import MemoryCapability


class MemoryCapabilityRegistry(BaseModel):
    """Memory Capability 集合的不可变注册表。

    构造约束（model_post_init）：
      - 每个 MemoryCapability 的 task_type 在 Registry 内唯一；
      - 每个 eligible_tool 在 Registry 内唯一（不允许两个 capability 注册同一 tool）；
      - 所有 capability 的 task_type / eligible_tools 字符串均合法（由
        MemoryCapability 自身的不变式保证）。

    API：
      - task_types()              —— Registry 中所有 task_type 元组；
      - eligible_tools()          —— Registry 中所有 eligible tool 元组；
      - tool_mapping()            —— tool_name → task_type 字典（与
                                     MemoryTaskTypePolicy.tool_to_task_type 等价）；
      - default_task_type()       —— 兜底 task_type（GENERIC）；
      - describe()                —— 人类可读快照（审计用）。

    禁止：
      - mutate；任何字段修改尝试都会因 frozen=True 抛 ValidationError。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    capabilities: tuple[MemoryCapability, ...] = Field(default_factory=tuple)
    default_task_type: str = 'GENERIC'

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        if not self.default_task_type or not self.default_task_type.strip():
            raise ValueError(
                'MemoryCapabilityRegistry.default_task_type 必须为非空字符串'
            )

        seen_task_types: set[str] = set()
        seen_tools: set[str] = set()
        for cap in self.capabilities:
            if not isinstance(cap, MemoryCapability):
                raise TypeError(
                    'MemoryCapabilityRegistry.capabilities 项必须为 MemoryCapability，'
                    f'得到 {type(cap).__name__}'
                )
            if cap.task_type in seen_task_types:
                raise ValueError(
                    f'MemoryCapabilityRegistry 注册了重复的 task_type={cap.task_type!r}；'
                    '每个业务必须使用独立 task_type'
                )
            seen_task_types.add(cap.task_type)

            for tool in cap.eligible_tools:
                if tool in seen_tools:
                    raise ValueError(
                        f'MemoryCapabilityRegistry 注册了重复的 eligible_tool={tool!r}；'
                        '同一 tool 不允许绑定多个 task_type（避免歧义）'
                    )
                seen_tools.add(tool)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def task_types(self) -> tuple[str, ...]:
        """Registry 中所有 task_type 集合（按注册顺序）。"""
        return tuple(cap.task_type for cap in self.capabilities)

    def eligible_tools(self) -> tuple[str, ...]:
        """Registry 中所有 eligible tool 集合（按注册顺序；可能跨 capability）。"""
        out: list[str] = []
        for cap in self.capabilities:
            for tool in cap.eligible_tools:
                if tool not in out:
                    out.append(tool)
        return tuple(out)

    def tool_mapping(self) -> dict[str, str]:
        """tool_name → task_type 映射。

        Registry 阶段已经保证每个 tool 唯一对应一个 capability，因此
        ``tool_mapping()`` 是确定性映射，可直接交给
        ``MemoryTaskTypePolicy.create_from_registry`` 使用。
        """
        mapping: dict[str, str] = {}
        for cap in self.capabilities:
            for tool in cap.eligible_tools:
                mapping[tool] = cap.task_type
        return mapping

    def default_task_type_value(self) -> str:
        """Registry 显式声明的兜底 task_type。"""
        return self.default_task_type

    def describe(self) -> str:
        """人类可读快照（审计 / 调试）。"""
        lines: list[str] = [
            'MemoryCapabilityRegistry:',
            f'  default_task_type = {self.default_task_type!r}',
            f'  capabilities ({len(self.capabilities)}):',
        ]
        if not self.capabilities:
            lines.append('    (empty)')
        for cap in self.capabilities:
            tools = ', '.join(sorted(cap.eligible_tools))
            lines.append(f'    - {cap.task_type}  tools=[{tools}]')
            if cap.description:
                lines.append(f'        description: {cap.description}')
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # 构造助手
    # ------------------------------------------------------------------

    @classmethod
    def of(
        cls,
        capabilities: list[MemoryCapability] | tuple[MemoryCapability, ...] = (),
        default_task_type: str = 'GENERIC',
    ) -> 'MemoryCapabilityRegistry':
        """显式构造 Registry 的便捷工厂。

        设计意图：
          - 业务方在 bootstrap 阶段显式声明能力列表；
          - 不存在"运行时注册"或"插件扫描"等隐式入口；
          - 构造失败时由 Pydantic ValidationError / ValueError 抛错（fail-loud）。

        强制类型校验：items 必须为 ``MemoryCapability`` 实例；dict / str 等
        不会被 Pydantic 自动 coerce（避免"看似 dict 但被吞掉"的隐性错误）。
        """
        normalized: list[MemoryCapability] = []
        for idx, item in enumerate(capabilities):
            if not isinstance(item, MemoryCapability):
                # 显式拒绝 dict / str / None 等非 MemoryCapability 输入
                if isinstance(item, type) and issubclass(item, MemoryCapability):
                    # 允许传入 MemoryCapability 类（class object），便于测试
                    # 与动态注册场景；构造为无字段实例可能失败，由 Pydantic 兜底。
                    raise ValueError(
                        'MemoryCapabilityRegistry.of() 收到 MemoryCapability 类，'
                        '请传入实例（MemoryCapability(...)）而非类型本身'
                    )
                raise TypeError(
                    'MemoryCapabilityRegistry.of() 收到非法项 '
                    f'#{idx}: 必须是 MemoryCapability 实例，得到 {type(item).__name__}'
                )
            normalized.append(item)
        return cls(
            capabilities=tuple(normalized),
            default_task_type=default_task_type,
        )


__all__ = ['MemoryCapabilityRegistry']