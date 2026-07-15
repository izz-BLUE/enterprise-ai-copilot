import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.agents.langgraph_agent import run_langgraph_agent
from app.core.concurrency import ConcurrencyLimitExceeded, ai_request_limiter
from app.core.config import DEEPSEEK_MODEL, MAX_MESSAGE_LENGTH, logger
from app.schemas.chat_schema import AgentResponse, ChatRequest, ChatResponse
from app.schemas.version_schema import VersionResponse
from app.services.rag_service import process_chat

app = FastAPI(title='Agent Python Service')

_BOUNDED_AI_PATHS = {'/agent/chat', '/agent/langgraph/chat'}


def _busy_response(path: str, trace_id: str) -> JSONResponse:
    common_headers = {'X-Trace-Id': trace_id, 'Retry-After': '1'}
    if path == '/agent/langgraph/chat':
        content = {
            'answer': '当前请求较多，请稍后重试。',
            'route': 'busy',
            'safe': True,
            'category': 'overloaded',
            'reason': '',
            'sources': [],
            'success': False,
            'traceId': trace_id,
        }
    else:
        content = {
            'answer': '当前请求较多，请稍后重试。',
            'model': DEEPSEEK_MODEL or 'unknown',
            'traceId': trace_id,
            'success': False,
        }
    return JSONResponse(status_code=429, content=content, headers=common_headers)


@app.middleware('http')
async def trace_id_middleware(request: Request, call_next):
    """Attach traceId and bound admission to expensive AI request paths."""
    trace_id = request.headers.get('x-trace-id')
    if not trace_id:
        trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id

    acquired = False
    if request.method == 'POST' and request.url.path in _BOUNDED_AI_PATHS:
        try:
            request.state.queue_wait_ms = await ai_request_limiter.acquire(trace_id)
            acquired = True
        except ConcurrencyLimitExceeded:
            return _busy_response(request.url.path, trace_id)

    try:
        response: Response = await call_next(request)
    finally:
        if acquired:
            ai_request_limiter.release(trace_id)

    response.headers['X-Trace-Id'] = trace_id
    return response


@app.get('/agent/health')
def health():
    return {
        'service': 'agent-python',
        'status': 'UP',
        'concurrency': ai_request_limiter.snapshot(),
    }


@app.get('/agent/version', response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        service='agent-python',
        version=os.getenv('APP_VERSION', 'dev'),
        gitCommit=os.getenv('GIT_COMMIT', 'unknown'),
        buildTime=os.getenv('BUILD_TIME', 'unknown'),
    )


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
        result = run_langgraph_agent(
            request.message, allow_eval=allow_eval, trace_id=trace_id,
        )
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
    except Exception:
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
