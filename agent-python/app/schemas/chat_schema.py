from pydantic import BaseModel, Field

from app.schemas.action_schema import AnnualLeaveActionProposal


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    model: str
    traceId: str
    success: bool


class AgentResponse(BaseModel):
    answer: str
    route: str
    safe: bool
    category: str
    reason: str
    sources: list = Field(default_factory=list)
    success: bool
    traceId: str = ""
    action_proposal: AnnualLeaveActionProposal | None = None
    missing_fields: list[str] = Field(default_factory=list)
