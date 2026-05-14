import os
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("环境变量 DEEPSEEK_API_KEY 未配置，请在 .env 文件中设置")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

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
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": "你是一个企业 AI 助手，回答要简洁、准确。"},
            {"role": "user", "content": request.message},
        ],
    )
    return ChatResponse(
        answer=response.choices[0].message.content,
        model=DEEPSEEK_MODEL,
        traceId=str(uuid4()),
    )
