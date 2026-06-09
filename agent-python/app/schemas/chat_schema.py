from pydantic import BaseModel


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
    sources: list = []
    success: bool
    traceId: str = ""
