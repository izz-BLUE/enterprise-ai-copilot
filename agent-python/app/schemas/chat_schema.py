from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.action_schema import AnnualLeaveActionProposal
from app.schemas.expense_schema import ExpenseActionProposal


class MemoryContext(BaseModel):
    """Java → Python 内部请求体的 memoryContext 字段。

    Scoped Conversation Memory / Task Continuity P0 —— Phase 2 (Read Path)。
    该字段由 Java 服务端基于 (trusted user_id, conversation_id) 复合 key
    在 status=ACTIVE 时填充；Python 不接受任何身份字段（userId / conversationId）
    或业务字段（nonce / idempotencyKey）。
    """

    model_config = ConfigDict(extra='ignore')

    taskType: str | None = None
    status: str | None = None
    taskStateJson: str | None = None
    summary: str | None = None


class ChatRequest(BaseModel):
    """Python 内部 Agent 请求 schema。

    Phase 2 重构后，Java 服务端通过 body 传递 memoryContext；前端不接触此字段。
    - message: 公开用户输入（与公共 ChatRequest 一致）。
    - memoryContext: 仅由 Java 服务端填充；缺失/None 时 Planner 行为与历史一致。
    """

    message: str
    memoryContext: MemoryContext | None = None


class ChatResponse(BaseModel):
    answer: str
    model: str
    traceId: str
    success: bool
    sources: list[str] = Field(default_factory=list)


class AgentMemoryProposal(BaseModel):
    """Python → Java 的非权威任务记忆提案。

    owner、conversationId 与生命周期状态均不属于该契约：Java 使用当前已认证
    请求上下文决定 owner，并固定按 UPSERT + ACTIVE 持久化。
    """

    model_config = ConfigDict(extra='forbid')

    task_type: str = Field(default='GENERIC', max_length=64)
    task_state: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default='', max_length=500)


class AgentResponse(BaseModel):
    answer: str
    route: str
    safe: bool
    category: str
    reason: str
    sources: list = Field(default_factory=list)
    success: bool
    traceId: str = ""
    # P2-A: 业务动作 Proposal 多态（V2 §十五）—— AnnualLeave | Expense
    action_proposal: AnnualLeaveActionProposal | ExpenseActionProposal | None = None
    missing_fields: list[str] = Field(default_factory=list)
    memory_proposal: AgentMemoryProposal | None = None
