"""p0_default_capabilities.py —— P0 默认 MemoryCapability 声明（P1-B）

P1-B 让业务模块以 ``MemoryCapability`` 形态声明自身 Memory 接入元数据。
P0 行为（GENERIC / LEAVE_REQUEST / BUSINESS_ACTION + leave_proposal_tool）
等价于以下 capability 集合：

    - LeaveMemoryCapability(task_type='LEAVE_REQUEST',
                            eligible_tools=frozenset({'leave_proposal_tool'}))
    - GenericMemoryCapability(task_type='GENERIC')
    - BusinessActionMemoryCapability(task_type='BUSINESS_ACTION')

本模块仅承载这些 capability 的"声明式样例"，方便业务方参考与回放；
它**不实现任何业务逻辑**（不调用 Tool / 不接 Planner / 不引入业务字段）。

不引入：

  * 不实现 Expense / Travel / Procurement 等业务（这些留给业务方各自声明）；
  * 不引入 Plugin System / Service Discovery / Remote Registry。
"""

from __future__ import annotations

from app.capabilities.memory_capability import MemoryCapability


# ---------------------------------------------------------------------------
# P0 capability 声明样例
# ---------------------------------------------------------------------------


GENERIC_MEMORY_CAPABILITY = MemoryCapability(
    task_type='GENERIC',
    eligible_tools=frozenset(),  # GENERIC 不绑定任何 tool（兜底类型）
    description='P0 默认兜底 task_type；不带任何 eligible tool。',
)


LEAVE_MEMORY_CAPABILITY = MemoryCapability(
    task_type='LEAVE_REQUEST',
    eligible_tools=frozenset({'leave_proposal_tool'}),
    description='P0 已存在的请假业务 capability；eligible tool = leave_proposal_tool。',
)


# 注意：BUSINESS_ACTION 是 P0 schema Literal 中的类别，但不绑定任何 tool。
# 留空 eligible_tools（语义：兜底类别，不触发 Memory 写入路径）。
BUSINESS_ACTION_MEMORY_CAPABILITY = MemoryCapability(
    task_type='BUSINESS_ACTION',
    eligible_tools=frozenset(),
    description='P0 通用业务动作 task_type；不绑定业务 tool，作为 fallback 类别。',
)


# 默认 P0 capability 集合（供 MemoryTaskTypePolicy.create_from_registry / 默认兼容使用）
DEFAULT_P0_CAPABILITIES: tuple[MemoryCapability, ...] = (
    GENERIC_MEMORY_CAPABILITY,
    LEAVE_MEMORY_CAPABILITY,
    BUSINESS_ACTION_MEMORY_CAPABILITY,
)


__all__ = [
    'BUSINESS_ACTION_MEMORY_CAPABILITY',
    'DEFAULT_P0_CAPABILITIES',
    'GENERIC_MEMORY_CAPABILITY',
    'LEAVE_MEMORY_CAPABILITY',
]