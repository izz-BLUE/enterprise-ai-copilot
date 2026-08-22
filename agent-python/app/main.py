import os
import uuid
from datetime import date
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.agents.langgraph_agent import run_langgraph_agent
from app.core.concurrency import ConcurrencyLimitExceeded, ai_request_limiter
from app.core.config import (
    AGENT_LOOP_ENABLED,
    DEEPSEEK_MODEL,
    JAVA_BASE_URL,
    JAVA_INTERNAL_TOKEN,
    JAVA_TIMEOUT_SECONDS,
    MEMORY_WRITE_MODE,
    MAX_MESSAGE_LENGTH,
    logger,
)
from app.clients.java_memory_client import JavaMemoryClient
from app.memory.memory_llm_adapter import MemoryLLMAdapter
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_runtime_hook import MemoryRuntimeHook
from app.memory.memory_write_dispatcher import MemoryWriteDispatcher
from app.memory.memory_write_mode import make_execution_policy
from app.schemas.chat_schema import AgentResponse, ChatRequest, ChatResponse
from app.schemas.version_schema import VersionResponse
from app.services.llm_service import call_llm
from app.services.rag_service import process_chat

app = FastAPI(title='Agent Python Service')

_BOUNDED_AI_PATHS = {'/agent/chat', '/agent/langgraph/chat'}

# 配置在 import 时 fail-closed 校验；模式不在白名单时服务不应静默运行。
_memory_execution_policy = make_execution_policy(MEMORY_WRITE_MODE)


class _JavaMemoryHttpClient:
    """为 JavaMemoryClient 注入固定超时的最小 HTTP 适配器。"""

    def __init__(self, timeout_seconds: int) -> None:
        self._timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)

    def post(
        self,
        url: str,
        json: dict[str, Any],  # noqa: A002
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return httpx.post(url, json=json, headers=headers, timeout=self._timeout)


def _unavailable_memory_writer(command: Any) -> None:
    """ENABLED 缺少 Java scope/config 时的 fail-closed writer。"""
    raise RuntimeError('Memory Java writer 配置或 trusted scope 不完整')


def _build_memory_runtime_hook(
    *,
    conversation_id: str,
    scope_token: str,
    trace_id: str,
) -> MemoryRuntimeHook | None:
    """按请求组装真实 Memory Pipeline 与 Java writer。

    scope_token 来自 Java 已验证请求的内部 header；Python 不解析、不生成、不改写
    user_id。DISABLED 在调用本函数前由 endpoint 短路，保证零额外 Extractor 成本。
    """
    if _memory_execution_policy.mode == 'DISABLED':
        return None

    pipeline = MemoryPipeline(llm_callable=MemoryLLMAdapter(call_llm))
    if (
        _memory_execution_policy.mode == 'ENABLED'
        and JAVA_BASE_URL
        and JAVA_INTERNAL_TOKEN
        and conversation_id
        and scope_token
    ):
        writer = JavaMemoryClient(
            http_client=_JavaMemoryHttpClient(JAVA_TIMEOUT_SECONDS),
            base_url=JAVA_BASE_URL,
            conversation_id=conversation_id,
            internal_token=JAVA_INTERNAL_TOKEN,
            scope_token=scope_token,
            trace_id=trace_id,
        )
        dispatcher = MemoryWriteDispatcher(writer=writer)
    elif _memory_execution_policy.mode == 'ENABLED':
        # 不把 Dispatcher 的无 writer noop 当成“写入成功”。
        dispatcher = MemoryWriteDispatcher(writer=_unavailable_memory_writer)
    else:
        # AUDIT_ONLY 不需要任何 Java writer，也不会产生 HTTP 请求。
        dispatcher = MemoryWriteDispatcher()

    return MemoryRuntimeHook(
        pipeline=pipeline,
        dispatcher=dispatcher,
        write_execution_policy=_memory_execution_policy,
    )


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


def _memory_context_to_dict(memory_context) -> dict | None:
    """把 Pydantic MemoryContext 转成 dict，供 Planner 注入 use_prompt。

    Pydantic v2 的 model_dump() 已保证字段白名单（extra='ignore'），
    不会泄漏 userId / conversationId / nonce 等敏感字段。
    任何字段缺失或为 None → 返回 None（即"无 Memory"）。
    """
    if memory_context is None:
        return None
    data = memory_context.model_dump(exclude_none=True)
    if not data:
        return None
    return data


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
    allow_business_actions = (
        req.headers.get('x-allow-business-actions', 'false').lower() == 'true'
    )
    business_date = None
    business_date_header = req.headers.get('x-business-date')
    if business_date_header:
        try:
            business_date = date.fromisoformat(business_date_header)
        except ValueError:
            pass

    # 企业 Tool P0：Java 侧已通过身份校验后注入的 employeeId。
    # 该值由 LangGraphAgentController 从 DemoIdentity 解析后写入 header，
    # Python 不接受任何来自请求体 / LLM arguments 的 employeeId。
    employee_id = (req.headers.get('x-employee-id') or '').strip()

    # Scoped Conversation Memory / Task Continuity P0 — Phase 2 (Read Path)。
    # Java 侧 LangGraphAgentController 已基于 (trusted user_id, conversation_id)
    # 复合 key 只在 status=ACTIVE 时填充 body.memoryContext 字段；本端点直接读取。
    # 不进入 Safety Guard 二次扫描，不修改任何 trusted 系统字段。
    memory_context = _memory_context_to_dict(request.memoryContext)

    try:
        result = run_langgraph_agent(
            request.message,
            allow_eval=allow_eval,
            allow_business_actions=allow_business_actions,
            business_date=business_date,
            trace_id=trace_id,
            employee_id=employee_id,
            use_planner=AGENT_LOOP_ENABLED,
            memory_context=memory_context,
        )

        # Memory 写入是出口层旁路；任何 Pipeline / Java 失败都不得阻断主响应。
        conversation_id = req.headers.get('x-conversation-id', '')
        if _memory_execution_policy.mode != 'DISABLED':
            memory_hook = _build_memory_runtime_hook(
                conversation_id=conversation_id,
                scope_token=req.headers.get('x-memory-write-scope', ''),
                trace_id=trace_id,
            )
            if memory_hook is not None:
                memory_hook.after_agent_response(result, conversation_id)

        return AgentResponse(
            answer=result.get('answer', ''),
            route=result.get('route', ''),
            safe=result.get('safe', True),
            category=result.get('category', ''),
            reason=result.get('reason', ''),
            sources=result.get('sources', []),
            success=result.get('route') != 'error',
            traceId=trace_id,
            action_proposal=result.get('action_proposal'),
            missing_fields=result.get('missing_fields', []),
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
