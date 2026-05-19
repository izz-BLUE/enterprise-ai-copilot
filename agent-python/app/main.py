from fastapi import FastAPI

from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.services.rag_service import process_chat

app = FastAPI(title='Agent Python Service')


@app.get('/agent/health')
def health():
    return {'service': 'agent-python', 'status': 'UP'}


@app.post('/agent/chat')
def chat(request: ChatRequest) -> ChatResponse:
    return process_chat(request.message)
