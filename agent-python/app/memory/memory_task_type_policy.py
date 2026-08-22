"""memory_task_type_policy.py —— Memory Task Type 控制可扩展白名单（Phase P1-A / P1-B）

P0 阶段 ``app.schemas.memory_schema.MemoryTaskType`` 是 Literal 闭集，
新增任何业务（例如 EXPENSE_REQUEST）必须修改 Schema 字段枚举，
等于修改 Memory Contract。

P1-A 目标：
  把 taskType 从 ``Closed Enum`` 演进为 ``Validated Extensible Task Type``，
  满足以下不变量：

    1. 类型校验仍然存在（LLM 输出 taskType 必须命中白名单）；
    2. 白名单能力保留（policy 是唯一允许集合的真理来源）；
    3. 不允许动态任意字符串（policy 必须显式声明可用集合）；
    4. 不修改 Java Memory Endpoint / Database / Runtime Hook /
       Dispatcher / AgentState（与 P1-A 禁止条款对齐）；
    5. taskType 不再由 Schema 闭集约束，由 Policy 控制集合；
    6. 不让 ``MemoryTaskType`` 自身承载所有业务 —— policy 是注册点，
       Schema 只承载契约形状。

P1-B 演进（Workflow Capability Registration Boundary）：
  之前：调用方（main.py / bootstrap 层）手动 ``MemoryTaskTypePolicy.create_for(...)``
        拼装扩展参数，导致 application bootstrap 中累积大量业务注册代码。
  现在：业务模块声明 ``MemoryCapability``（纯元数据），由 ``MemoryCapabilityRegistry``
        汇总，再交给 ``MemoryTaskTypePolicy.create_from_registry(registry)`` 消费。

  业务扩展路径（新增 EXPENSE_REQUEST）：
    1. 业务方在 ``app/capabilities/expense_capability.py`` 声明：
         MemoryCapability(task_type='EXPENSE_REQUEST', eligible_tools={'expense_proposal_tool'})
    2. main.py 在 bootstrap 阶段：
         registry = MemoryCapabilityRegistry.of([LeaveCapability(), ExpenseCapability()])
         policy   = MemoryTaskTypePolicy.create_from_registry(registry)
         pipeline = MemoryPipeline(task_type_policy=policy)
    3. Memory Core（memory_extractor / memory_trigger_policy / memory_write_policy /
       memory_pipeline）通过 policy 间接消费 Registry；**不 import 任何业务模块**。

  不修改：
    - app.schemas.memory_schema.MemoryTaskType（保留为兼容历史调用）；
    - ai_task_memory 表结构；
    - Java Memory Endpoint / Contract；
    - Runtime Hook / Dispatcher / AgentState；
    - memory core 4 个模块（memory_extractor / memory_trigger_policy /
      memory_write_policy / memory_pipeline）的逻辑签名；
      它们继续消费 ``MemoryTaskTypePolicy``，policy 改为消费 Registry
      形成 "Business → Capability → Registry → Policy → Memory Core" 链。

设计要点：

  * 默认 policy 等价于 P0 行为：
      - available_task_types = ('GENERIC', 'LEAVE_REQUEST', 'BUSINESS_ACTION')
      - eligible_tool_names   = frozenset({LEAVE_PROPOSAL_TOOL_NAME})
      - tool_to_task_type     = {LEAVE_PROPOSAL_TOOL_NAME: 'LEAVE_REQUEST'}
    现有触发 / 写入 / 测试契约保持不变。

  * Trust 边界：
      - MemoryTaskTypePolicy 持有白名单集合（trusted program-level state）；
      - 不接受任何运行时 / LLM / taskState 注入的 taskType —— policy 是
        唯一真理来源，调用方只能 "set once at construction"。
      - MemoryWritePolicy / MemoryTriggerPolicy / MemoryExtractor 必须
        在构造时持有 policy 引用，并在每次评估时调用 policy.is_allowed(taskType)
        而非直接信任 LLM 输出。
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field


# 默认 task_type 白名单（与 P0 MemoryTaskType Literal 保持一致）。
# 任何新增项必须显式注册（见 create_for / create_from_registry），
# schema / Java / DB 不需要改。
DEFAULT_TASK_TYPES: tuple[str, ...] = (
    'GENERIC',
    'LEAVE_REQUEST',
    'BUSINESS_ACTION',
)

# 默认 taskType 缺省值（与 Java 侧 AiTaskMemoryService.DEFAULT_TASK_TYPE 对齐）。
DEFAULT_TASK_TYPE = 'GENERIC'

# 默认 tool → taskType 映射（P0 行为：仅 leave_proposal_tool 触发 Memory 写入）。
# 引入 ``eligible_tool_names`` 概念后，Trigger Policy 只看 "Tool 名字是否注册为
# 具备跨请求任务连续性价值的 Memory-eligible tool"，而不关心业务硬编码。
DEFAULT_TOOL_TO_TASK_TYPE: dict[str, str] = {
    # Planner-first 链路下，业务动作统一通过 leave_proposal_tool 走受控链路；
    # 该 Tool 触发的写入任务类型是 LEAVE_REQUEST。
    'leave_proposal_tool': 'LEAVE_REQUEST',
}


class MemoryTaskTypePolicy(BaseModel):
    """Memory Task Type 控制可扩展白名单。

    属性：
      available_task_types —— 当前 policy 允许写入的 task_type 集合（元组，按注册顺序）。
      tool_to_task_type    —— tool_name → task_type 的注册映射；
                              key 集合隐式表达 "Memory-eligible tool 白名单"，
                              即 eligible_tool_names() = frozenset(tool_to_task_type)。
      default_task_type    —— 当 LLM/Extractor 未提供 task_type 时使用的兜底值；
                              必须 ∈ available_task_types（构造时校验）。

    不变式：
      - available_task_types 非空；
      - tool_to_task_type 的 value 全部 ∈ available_task_types；
      - default_task_type ∈ available_task_types；
      - 一旦构造完成不可变（frozen=True），避免运行期动态扩展破坏可审计性。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    available_task_types: tuple[str, ...] = Field(
        default_factory=lambda: DEFAULT_TASK_TYPES,
    )
    tool_to_task_type: dict[str, str] = Field(default_factory=dict)
    default_task_type: str = DEFAULT_TASK_TYPE

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> 'MemoryTaskTypePolicy':
        """构造与 P0 等价的默认 policy（保持后向兼容）。"""
        return cls(
            available_task_types=DEFAULT_TASK_TYPES,
            tool_to_task_type=dict(DEFAULT_TOOL_TO_TASK_TYPE),
            default_task_type=DEFAULT_TASK_TYPE,
        )

    @classmethod
    def create_for(
        cls,
        extra_task_types: Iterable[str] = (),
        extra_tool_to_task_type: dict[str, str] | None = None,
        default_task_type: str | None = None,
    ) -> 'MemoryTaskTypePolicy':
        """基于默认 policy 扩展；用于新增业务（例如 EXPENSE_REQUEST）注册。

        参数：
          extra_task_types          —— 在默认集合上追加的新 task_type；
                                        已存在的会被去重（保持原顺序）。
          extra_tool_to_task_type   —— 在默认 tool → taskType 映射上叠加；
                                        value 必须命中（扩展后的）
                                        available_task_types，否则抛 ValueError。
          default_task_type         —— 可选替换默认兜底 task_type；必须
                                        ∈ 扩展后的 available_task_types。

        抛出：
          ValueError —— 任何注册项与已有集合冲突或 value 不在白名单内。

        P1-B 替代路径：
          业务方不再通过 ``create_for`` 拼装参数；改用 ``create_from_registry``
          + ``MemoryCapabilityRegistry``。本方法保留仅用于"一次性脚本 /
          调试 / 不引入 Registry 的迁移路径"，业务 bootstrap 不应使用。
        """
        # 1. 合并 task_types（保留 P0 顺序，去重）
        merged_types: list[str] = list(DEFAULT_TASK_TYPES)
        for t in extra_task_types:
            if not isinstance(t, str) or not t:
                raise ValueError(
                    f'extra_task_types 项必须为非空字符串，得到 {t!r}'
                )
            if t not in merged_types:
                merged_types.append(t)

        # 2. 合并 tool → task_type 映射
        merged_tool_map: dict[str, str] = dict(DEFAULT_TOOL_TO_TASK_TYPE)
        if extra_tool_to_task_type:
            for tool_name, task_type in extra_tool_to_task_type.items():
                if not isinstance(tool_name, str) or not tool_name:
                    raise ValueError(
                        f'extra_tool_to_task_type 的 key 必须为非空字符串，得到 {tool_name!r}'
                    )
                if task_type not in merged_types:
                    raise ValueError(
                        f'extra_tool_to_task_type[{tool_name!r}] = {task_type!r} '
                        f'不在 available_task_types {merged_types!r} 中'
                    )
                merged_tool_map[tool_name] = task_type

        # 3. default_task_type 校验
        final_default = default_task_type or DEFAULT_TASK_TYPE
        if final_default not in merged_types:
            raise ValueError(
                f'default_task_type={final_default!r} 不在 '
                f'available_task_types {merged_types!r} 中'
            )

        return cls(
            available_task_types=tuple(merged_types),
            tool_to_task_type=merged_tool_map,
            default_task_type=final_default,
        )

    @classmethod
    def create_from_registry(
        cls,
        registry: 'MemoryCapabilityRegistry',  # noqa: F821
        *,
        include_default_p0: bool = True,
        default_task_type: str | None = None,
    ) -> 'MemoryTaskTypePolicy':
        """从 ``MemoryCapabilityRegistry`` 构造 MemoryTaskTypePolicy（P1-B 入口）。

        行为：
          - ``include_default_p0=True``（默认）：在 Registry 的 capability 之上
            叠加 P0 默认 task_type 集合（GENERIC / LEAVE_REQUEST / BUSINESS_ACTION）
            与 P0 默认 tool → taskType 映射（leave_proposal_tool → LEAVE_REQUEST），
            保持 P0 向后兼容。
          - ``include_default_p0=False``：严格按 Registry 内容构造 policy，
            Registry 必须显式声明所有 task_type 与 eligible tool；不留隐性 P0 兜底。
          - ``default_task_type``：可选指定兜底 task_type；
            缺省时优先使用 ``registry.default_task_type_value()``（默认 GENERIC）；
            必须 ∈ 扩展后的 available_task_types。

        抛出：
          - TypeError —— registry 不是 MemoryCapabilityRegistry；
          - ValueError —— 任何 task_type / tool 冲突或 default 不在集合内。

        设计意图：
          把"业务注册 Memory 能力"的认知负担从 application bootstrap 转移到
          业务模块自身声明；policy 仅消费 Registry。
        """
        # 延迟 import：避免 app.memory 与 app.capabilities 形成"双核心"互相依赖；
        # policy 是核心消费者，capabilities 是被消费者，依赖方向单向。
        from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry

        if not isinstance(registry, MemoryCapabilityRegistry):
            raise TypeError(
                'MemoryTaskTypePolicy.create_from_registry 需要 '
                f'MemoryCapabilityRegistry 输入，得到 {type(registry).__name__}'
            )

        # 1. 收集 task_types（按注册顺序）
        cap_task_types: list[str] = list(registry.task_types())

        # 2. 收集 tool → task_type 映射（registry 已保证 tool 唯一）
        cap_tool_map: dict[str, str] = dict(registry.tool_mapping())

        # 3. 是否叠加 P0 默认
        if include_default_p0:
            merged_types: list[str] = list(DEFAULT_TASK_TYPES)
            for t in cap_task_types:
                if t not in merged_types:
                    merged_types.append(t)
            merged_tool_map: dict[str, str] = dict(DEFAULT_TOOL_TO_TASK_TYPE)
            # 如果 Registry 显式声明 LEAVE_REQUEST capability，移除默认的
            # leave_proposal_tool 映射以避免冲突（registry 唯一性已保证）。
            if 'LEAVE_REQUEST' in cap_task_types:
                merged_tool_map.pop('leave_proposal_tool', None)
            for tool, task_type in cap_tool_map.items():
                merged_tool_map[tool] = task_type
        else:
            merged_types = cap_task_types
            merged_tool_map = cap_tool_map

        # 4. default_task_type 校验
        if default_task_type is None:
            default_task_type = registry.default_task_type_value()
        if default_task_type not in merged_types:
            raise ValueError(
                f'default_task_type={default_task_type!r} 不在 '
                f'available_task_types {merged_types!r} 中'
            )

        return cls(
            available_task_types=tuple(merged_types),
            tool_to_task_type=merged_tool_map,
            default_task_type=default_task_type,
        )

    # ------------------------------------------------------------------
    # 校验 / 查询
    # ------------------------------------------------------------------

    def is_allowed(self, task_type: str | None) -> bool:
        """判断 task_type 是否在白名单内。None / 空字符串视为不合法。"""
        if not task_type or not isinstance(task_type, str):
            return False
        return task_type in self.available_task_types

    def assert_allowed(self, task_type: str | None) -> None:
        """断言 task_type 合法；非法抛 ValueError（fail-loud）。"""
        if not self.is_allowed(task_type):
            raise ValueError(
                f'不允许的 task_type={task_type!r}；'
                f'当前 policy 允许 {self.available_task_types!r}'
            )

    def eligible_tool_names(self) -> frozenset[str]:
        """Memory-eligible tool 白名单（= tool_to_task_type 的 key 集合）。

        用于 MemoryTriggerPolicy 判断 tool_history 命中哪些工具时触发 Extractor；
        不再硬编码 tool_name 白名单（与 P1-A Task 3 一致）。
        """
        return frozenset(self.tool_to_task_type.keys())

    def resolve_task_type(self, tool_name: str | None) -> str | None:
        """根据 tool_name 推导 task_type；未注册返回 None。"""
        if not tool_name or not isinstance(tool_name, str):
            return None
        return self.tool_to_task_type.get(tool_name)

    def fallback_task_type(self) -> str:
        """兜底 task_type（policy 显式声明的默认）。"""
        return self.default_task_type


__all__ = [
    'DEFAULT_TASK_TYPE',
    'DEFAULT_TASK_TYPES',
    'DEFAULT_TOOL_TO_TASK_TYPE',
    'MemoryTaskTypePolicy',
]