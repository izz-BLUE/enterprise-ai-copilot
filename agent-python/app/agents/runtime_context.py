"""一次 LangGraph 调用的可信上下文。

这些值由当前 Java -> Python 请求提供，不属于 :class:`AgentState`。绝不能从执行
snapshot 中恢复它们并将其作为可信输入。
"""

from datetime import date
from typing import Literal, TypedDict

ExecutionMode = Literal['LEGACY_SINGLE', 'TASK_RUNTIME']


class AgentRuntimeContext(TypedDict):
    """Agent 节点和 Tool Executor 使用的请求级可信输入。"""

    employee_id: str
    allow_eval: bool
    allow_business_actions: bool
    business_date: date | None
    trace_id: str
    deadline_monotonic: float
    execution_mode: ExecutionMode
