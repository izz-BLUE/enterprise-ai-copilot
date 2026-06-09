import uuid

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.schemas.chat_schema import AgentResponse, ChatRequest, ChatResponse
from app.services.rag_service import process_chat
from app.agents.langgraph_agent import run_langgraph_agent
from app.core.config import logger

app = FastAPI(title='Agent Python Service')


@app.middleware('http')
async def trace_id_middleware(request: Request, call_next):
    """从请求头读取 X-Trace-Id，没有则生成，写入 request.state 并设置响应头。"""
    trace_id = request.headers.get('x-trace-id')
    if not trace_id:
        trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id

    response: Response = await call_next(request)
    response.headers['X-Trace-Id'] = trace_id
    return response


@app.get('/agent/health')
def health():
    return {'service': 'agent-python', 'status': 'UP'}


@app.post('/agent/chat')
def chat(request: ChatRequest, req: Request) -> ChatResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到普通 RAG 请求: %s', trace_id, request.message)
    return process_chat(request.message, trace_id=trace_id)


@app.post('/agent/langgraph/chat')
def langgraph_chat(request: ChatRequest, req: Request) -> AgentResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到 LangGraph Agent 请求: %s', trace_id, request.message)
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
            traceId=trace_id,
        )
    except Exception as e:
        logger.exception('[%s] LangGraph Agent 异常', trace_id)
        return AgentResponse(
            answer='当前 Agent 服务暂时不可用，请稍后重试。',
            route='error',
            safe=True,
            category='error',
            reason=str(e),
            sources=[],
            success=False,
            traceId=trace_id,
        )
