import uuid

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.core.config import DEEPSEEK_MODEL, MAX_MESSAGE_LENGTH, logger
from app.schemas.chat_schema import AgentResponse, ChatRequest, ChatResponse
from app.services.rag_service import process_chat
from app.agents.langgraph_agent import run_langgraph_agent

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


def _validate_message_length(message: str, trace_id: str) -> bool:
    """检查消息长度是否超限。超限时返回 True。"""
    return len(message) > MAX_MESSAGE_LENGTH


@app.post('/agent/chat')
def chat(request: ChatRequest, req: Request) -> ChatResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到普通 RAG 请求 (len=%d)', trace_id, len(request.message))

    if _validate_message_length(request.message, trace_id):
        logger.warning('[%s] 输入过长 (len=%d > %d)', trace_id,
                       len(request.message), MAX_MESSAGE_LENGTH)
        return ChatResponse(
            answer='输入内容过长，请精简后重试。',
            model=DEEPSEEK_MODEL,
            traceId=trace_id,
            success=False,
        )

    return process_chat(request.message, trace_id=trace_id)


@app.post('/agent/langgraph/chat')
def langgraph_chat(request: ChatRequest, req: Request) -> AgentResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到 LangGraph Agent 请求 (len=%d)', trace_id, len(request.message))

    if _validate_message_length(request.message, trace_id):
        logger.warning('[%s] 输入过长 (len=%d > %d)', trace_id,
                       len(request.message), MAX_MESSAGE_LENGTH)
        return AgentResponse(
            answer='输入内容过长，请精简后重试。',
            route='error',
            safe=True,
            category='input_error',
            reason='',
            sources=[],
            success=False,
            traceId=trace_id,
        )

    allow_eval = req.headers.get('x-allow-eval', 'false').lower() == 'true'

    try:
        result = run_langgraph_agent(request.message, allow_eval=allow_eval)
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
            reason='',  # 不暴露异常细节，详情仅记日志
            sources=[],
            success=False,
            traceId=trace_id,
        )
