from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Agent Python Service")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    model: str
    traceId: str


@app.get("/agent/health")
def health():
    return {"service": "agent-python", "status": "UP"}


@app.post("/agent/chat")
def chat(request: ChatRequest) -> ChatResponse:
    return ChatResponse(
        answer=f"你好，这是 Python Agent 的模拟回答：{request.message}",
        model="mock-agent",
        traceId=str(uuid4()),
    )
