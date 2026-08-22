"""Scoped Conversation Memory Phase 5A 的离线评估 Case 契约。

本模块只描述评估输入，不参与 Memory Runtime。``turns`` 中的内容是离线
采集的观察记录；它们不是 AgentState，也不会被送回 LangGraph 或 Memory
Write Path。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.memory_schema import (
    MemoryProposalAction,
    MemoryProposalStatus,
    MemoryTaskType,
)


class MemoryEvaluationCase(BaseModel):
    """单个 Memory 场景的期望结果与离线观察记录。

    ``turns`` 保持为开放的观察记录列表：评估器只读取其中约定的信号字段，
    允许测试夹具同时保留用户输入、Tool 历史和 Memory 观察，而不改变任何
    生产数据契约。
    """

    model_config = ConfigDict(extra='forbid')

    case_id: str = Field(min_length=1)
    description: str = ''
    initial_context: dict[str, Any] = Field(default_factory=dict)
    turns: list[Any] = Field(default_factory=list)
    expected_trigger: bool
    expected_action: MemoryProposalAction | None
    expected_task_type: MemoryTaskType | None
    expected_status: MemoryProposalStatus | None
    expected_use_memory: bool
    expected_tool_behavior: Any = None
