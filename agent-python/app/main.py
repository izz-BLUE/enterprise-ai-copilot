from fastapi import FastAPI

from app.schemas.chat_schema import AgentResponse, ChatRequest, ChatResponse
from app.services.rag_service import process_chat
from app.agents.langgraph_agent import run_langgraph_agent

app = FastAPI(title='Agent Python Service')


@app.get('/agent/health')
def health():
    return {'service': 'agent-python', 'status': 'UP'}


@app.post('/agent/chat')
def chat(request: ChatRequest) -> ChatResponse:
    return process_chat(request.message)


@app.post('/agent/langgraph/chat')
def langgraph_chat(request: ChatRequest) -> AgentResponse:
    try:
        result = run_langgraph_agent(request.message)
        return AgentResponse(
            answer=result.get('answer', ''),
            route=result.get('route', ''),
            safe=result.get('safe', True),
            category=result.get('category', ''),
            reason=result.get('reason', ''),
            sources=result.get('sources', []),
            success=True,
        )
    except Exception as e:
        return AgentResponse(
            answer='当前 Agent 服务暂时不可用，请稍后重试。',
            route='error',
            safe=True,
            category='error',
            reason=str(e),
            sources=[],
            success=False,
        )
