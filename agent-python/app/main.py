import os
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL')

SYSTEM_PROMPT = '你是一个企业 AI Copilot 助手。\n你的职责是帮助企业员工理解制度、流程、知识库内容。\n回答要求：\n1. 回答要简洁、准确。\n2. 不要编造没有依据的信息。\n3. 如果不确定，请明确说明「当前信息不足，无法确定」。\n4. 优先使用分点说明。\n5. 如果用户询问具体制度、流程、规定，但当前没有提供知识库内容或依据，你必须提醒“当前未接入企业制度知识库，以下仅为通用建议，不能作为正式制度依据”。'

if not DEEPSEEK_API_KEY:
    raise RuntimeError('环境变量 DEEPSEEK_API_KEY 未配置，请在 .env 文件中设置')

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

app = FastAPI(title='Agent Python Service')


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    model: str
    traceId: str
    success: bool


@app.get('/agent/health')
def health():
    return {'service': 'agent-python', 'status': 'UP'}


@app.post('/agent/chat')
def chat(request: ChatRequest) -> ChatResponse:

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': request.message},
            ],
        )
        return ChatResponse(
            answer=response.choices[0].message.content,
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=True,
        )
    except Exception:
        return ChatResponse(
            answer='当前 AI 服务暂时不可用，请稍后重试。',
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=False,
        )
