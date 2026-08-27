import os
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.agents.langgraph_agent import resume_langgraph_agent, run_langgraph_agent
from app.capabilities.expense_capability import EXPENSE_MEMORY_CAPABILITY
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.capabilities.p0_default_capabilities import DEFAULT_P0_CAPABILITIES
from app.core.concurrency import ConcurrencyLimitExceeded, ai_request_limiter
from app.core.config import (
    AGENT_LOOP_ENABLED,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LANGGRAPH_CHECKPOINT_MODE,
    MAX_MESSAGE_LENGTH,
    MEMORY_WRITE_MODE,
    logger,
)
from app.core.observability import (
    initialize_observability,
    record_ai_response,
    shutdown_observability,
    trace_ai_request,
)
from app.memory.memory_llm_adapter import MemoryLLMAdapter
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_runtime_hook import MemoryRuntimeHook
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.memory.memory_write_dispatcher import MemoryWriteDispatcher
from app.memory.memory_write_mode import make_execution_policy
from app.memory.memory_write_policy import MemoryWriteCommand
from app.retrieval.chunk_store import chunk_store_status
from app.retrieval.faiss_retriever import faiss_status
from app.runtime.checkpoint_runtime import CheckpointRuntime
from app.runtime.execution_recovery import RecoveryMode
from app.schemas.chat_schema import (
    AgentMemoryProposal,
    AgentResponse,
    ChatRequest,
    ChatResponse,
)
from app.schemas.version_schema import VersionResponse
from app.services.llm_service import call_llm
from app.services.rag_service import process_chat


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_observability()
    checkpoint_runtime = CheckpointRuntime.from_config()
    try:
        checkpoint_runtime.start()
        _app.state.checkpoint_runtime = checkpoint_runtime
        yield
    finally:
        checkpoint_runtime.shutdown()
        shutdown_observability()


app = FastAPI(title='Agent Python Service', lifespan=lifespan)

_BOUNDED_AI_PATHS = {'/agent/chat', '/agent/langgraph/chat'}

# 配置在 import 时 fail-closed 校验；模式不在白名单时服务不应静默运行。
_memory_execution_policy = make_execution_policy(MEMORY_WRITE_MODE)


class _ResponseMemoryWriter:
    """收集 policy-approved command，随 Agent 响应返回给 Java。

    该 writer 不接触 HTTP、身份或数据库。Java 收到提案后使用当前请求的
    VerifiedIdentity 与 conversationId 决定真实持久化作用域。
    """

    def __init__(self) -> None:
        self.command: MemoryWriteCommand | None = None

    def __call__(self, command: MemoryWriteCommand) -> None:
        if command.action != 'UPSERT' or command.status != 'ACTIVE':
            raise RuntimeError('Memory response 只允许 UPSERT + ACTIVE')
        self.command = command


def _build_memory_capability_registry() -> MemoryCapabilityRegistry:
    """构造当前 Agent runtime 使用的 Memory capability registry。"""
    return MemoryCapabilityRegistry.of([
        *DEFAULT_P0_CAPABILITIES,
        EXPENSE_MEMORY_CAPABILITY,
    ])


def _build_memory_runtime_hook(
    *, trace_id: str,
) -> tuple[MemoryRuntimeHook, _ResponseMemoryWriter] | None:
    """按请求组装 Memory Pipeline 与响应内提案 writer。"""
    if _memory_execution_policy.mode == 'DISABLED':
        return None

    # P2-A (V2 §二十六): Capability Registry 是业务 eligibility 唯一真理来源。
    # P0 默认能力与应用层 Expense capability 显式合并；不修改
    # DEFAULT_TOOL_TO_TASK_TYPE（避免双重 hardcode）。
    registry = _build_memory_capability_registry()
    policy = MemoryTaskTypePolicy.create_from_registry(
        registry)
    pipeline = MemoryPipeline(task_type_policy=policy,
                              llm_callable=MemoryLLMAdapter(call_llm))
    writer = _ResponseMemoryWriter()
    dispatcher = MemoryWriteDispatcher(writer=writer)
    hook = MemoryRuntimeHook(
        pipeline=pipeline,
        dispatcher=dispatcher,
        write_execution_policy=_memory_execution_policy,
    )
    logger.debug('[%s] Memory response pipeline 已组装', trace_id)
    return hook, writer


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

    is_ai_request = request.method == 'POST' and request.url.path in _BOUNDED_AI_PATHS
    trace_context = trace_ai_request(
        method=request.method,
        path=request.url.path,
        business_trace_id=trace_id,
    ) if is_ai_request else nullcontext(None)
    with trace_context as span:
        acquired = False
        if is_ai_request:
            try:
                request.state.queue_wait_ms = await ai_request_limiter.acquire(trace_id)
                acquired = True
            except ConcurrencyLimitExceeded:
                response = _busy_response(request.url.path, trace_id)
                record_ai_response(span, status_code=429, queue_wait_ms=None)
                return response

        try:
            response: Response = await call_next(request)
        finally:
            if acquired:
                ai_request_limiter.release(trace_id)

        response.headers['X-Trace-Id'] = trace_id
        record_ai_response(
            span,
            status_code=response.status_code,
            queue_wait_ms=getattr(request.state, 'queue_wait_ms', None),
        )
        return response


@app.get('/agent/health')
def health():
    return {
        'service': 'agent-python',
        'status': 'UP',
        'checkpoint': {'mode': LANGGRAPH_CHECKPOINT_MODE},
        'concurrency': ai_request_limiter.snapshot(),
    }


@app.get('/agent/ready')
def readiness() -> Response:
    provider_ready = bool(DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL and DEEPSEEK_MODEL)
    chunks = chunk_store_status()
    vector = faiss_status()
    checkpoint_runtime = getattr(app.state, 'checkpoint_runtime', None)
    if checkpoint_runtime is None:
        checkpoint = {
            'enabled': LANGGRAPH_CHECKPOINT_MODE == 'POSTGRES',
            'ready': LANGGRAPH_CHECKPOINT_MODE == 'DISABLED',
        }
    else:
        checkpoint = checkpoint_runtime.readiness()
    checks = {
        'provider_config': {'ready': provider_ready},
        'chunks': chunks,
        'faiss': vector,
        'checkpoint': checkpoint,
    }
    ready = (
        provider_ready
        and bool(chunks['ready'])
        and bool(vector['ready'])
        and bool(checkpoint['ready'])
    )
    payload = {
        'service': 'agent-python',
        'status': 'READY' if ready else 'NOT_READY',
        'checks': checks,
        'concurrency': ai_request_limiter.snapshot(),
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


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


@app.post('/agent/chat', response_model=ChatResponse)
def chat(request: ChatRequest, req: Request) -> ChatResponse | JSONResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到普通 RAG 请求 (len=%d)', trace_id, len(request.message))

    if _validate_message_length(request.message, trace_id):
        logger.warning('[%s] 输入过长 (len=%d > %d)', trace_id,
                       len(request.message), MAX_MESSAGE_LENGTH)
        response = ChatResponse(
            answer='输入内容过长，请精简后重试。',
            model=DEEPSEEK_MODEL,
            traceId=trace_id,
            success=False,
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode='json'))

    response = process_chat(request.message, trace_id=trace_id)
    if not response.success:
        return JSONResponse(status_code=502, content=response.model_dump(mode='json'))
    return response


@app.post('/agent/langgraph/chat', response_model=AgentResponse)
def langgraph_chat(request: ChatRequest, req: Request) -> AgentResponse | JSONResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到 LangGraph Agent 请求 (len=%d)', trace_id, len(request.message))

    if _validate_message_length(request.message, trace_id):
        logger.warning('[%s] 输入过长 (len=%d > %d)', trace_id,
                       len(request.message), MAX_MESSAGE_LENGTH)
        response = AgentResponse(
            answer='输入内容过长，请精简后重试。',
            route='error',
            safe=True,
            category='input_error',
            reason='',
            sources=[],
            success=False,
            traceId=trace_id,
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode='json'))

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

    graph = None
    runtime_thread_id = None
    execution_history: list[dict] = []
    if LANGGRAPH_CHECKPOINT_MODE == 'POSTGRES':
        checkpoint_runtime = getattr(app.state, 'checkpoint_runtime', None)
        if checkpoint_runtime is None:
            logger.error('[%s] LangGraph checkpoint runtime 不可用', trace_id)
            return _checkpoint_failure_response(trace_id, status_code=503)
        try:
            # X-Agent-Thread-Id 只由 Java 根据可信 identity + resolved conversationId
            # 注入；缺失或格式不合法时 fail-closed，绝不自行生成或回退无快照模式。
            runtime_thread_id = checkpoint_runtime.build_thread_id(
                (req.headers.get('x-agent-thread-id') or '').strip(),
                use_planner=AGENT_LOOP_ENABLED,
            )
            graph = checkpoint_runtime.get_graph(use_planner=AGENT_LOOP_ENABLED)
        except (RuntimeError, ValueError):
            logger.warning('[%s] LangGraph checkpoint 请求上下文无效', trace_id)
            return _checkpoint_failure_response(trace_id, status_code=400)

    try:
        if graph is not None:
            # P3-2：同一个最终 thread_id 的 hydrate + invoke 必须处于同一进程内
            # guard 中；并发请求立即返回 429，让客户端重新走完整 Java 链路。
            if not checkpoint_runtime.try_acquire_thread(runtime_thread_id):
                return _busy_response('/agent/langgraph/chat', trace_id)
            try:
                recovery = None
                if AGENT_LOOP_ENABLED:
                    try:
                        recovery = checkpoint_runtime.inspect_recovery(
                            graph=graph,
                            thread_id=runtime_thread_id,
                            question=request.message,
                            business_date=business_date,
                        )
                    except Exception:
                        logger.exception('[%s] LangGraph recovery inspection 读取失败', trace_id)
                        return _checkpoint_failure_response(trace_id, status_code=503)

                    if recovery.is_conflict:
                        logger.warning(
                            '[%s] recovery_mode=CONFLICT reason=%s execution_id_prefix=%s pending_node=%s',
                            trace_id,
                            recovery.reason,
                            (recovery.execution_id or '')[:11],
                            recovery.pending_node or '-',
                        )
                        return _recovery_conflict_response(trace_id)

                    logger.info(
                        '[%s] recovery_mode=%s execution_id_prefix=%s pending_node=%s',
                        trace_id,
                        recovery.mode.value,
                        (recovery.execution_id or '')[:11],
                        recovery.pending_node or '-',
                    )

                if recovery is not None and recovery.mode is RecoveryMode.RESUME:
                    # Resume 使用 Checkpoint 中同一次 execution 的 state；不 hydrate
                    # P3-2 execution_history，也不重建 initial AgentState。
                    result = resume_langgraph_agent(
                        graph=graph,
                        runtime_thread_id=runtime_thread_id,
                        allow_eval=allow_eval,
                        allow_business_actions=allow_business_actions,
                        business_date=business_date,
                        trace_id=trace_id,
                        employee_id=employee_id,
                    )
                else:
                    try:
                        execution_history = checkpoint_runtime.load_execution_history(
                            graph=graph,
                            thread_id=runtime_thread_id,
                            memory_context=memory_context,
                        )
                    except Exception:
                        logger.exception('[%s] LangGraph execution history 读取失败', trace_id)
                        return _checkpoint_failure_response(trace_id, status_code=503)

                    result = run_langgraph_agent(
                        request.message,
                        allow_eval=allow_eval,
                        allow_business_actions=allow_business_actions,
                        business_date=business_date,
                        trace_id=trace_id,
                        employee_id=employee_id,
                        use_planner=AGENT_LOOP_ENABLED,
                        memory_context=memory_context,
                        execution_history=execution_history,
                        graph=graph,
                        runtime_thread_id=runtime_thread_id,
                    )
            finally:
                # Memory Pipeline 在 guard 外运行；它不是 Checkpoint 的写入步骤。
                checkpoint_runtime.release_thread(runtime_thread_id)
        else:
            agent_kwargs = {
                'allow_eval': allow_eval,
                'allow_business_actions': allow_business_actions,
                'business_date': business_date,
                'trace_id': trace_id,
                'employee_id': employee_id,
                'use_planner': AGENT_LOOP_ENABLED,
                'memory_context': memory_context,
            }
            result = run_langgraph_agent(request.message, **agent_kwargs)

        # Memory 提案是出口层旁路；任何 Pipeline 失败都不得阻断主响应。
        # Python 不再反向调用 Java，不持有 owner 签名能力；Java 在当前已认证请求内落库。
        conversation_id = req.headers.get('x-conversation-id', '')
        memory_proposal = None
        if _memory_execution_policy.mode != 'DISABLED':
            runtime = _build_memory_runtime_hook(trace_id=trace_id)
            if runtime is not None:
                memory_hook, response_writer = runtime
                runtime_result = memory_hook.after_agent_response(result, conversation_id)
                if runtime_result.written and response_writer.command is not None:
                    command = response_writer.command
                    memory_proposal = AgentMemoryProposal(
                        task_type=command.task_type,
                        task_state=command.task_state,
                        summary=command.summary,
                    )

        response = AgentResponse(
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
            memory_proposal=memory_proposal,
        )
        if not response.success:
            return JSONResponse(status_code=502, content=response.model_dump(mode='json'))
        return response
    except Exception:
        logger.exception('[%s] LangGraph Agent 异常', trace_id)
        response = AgentResponse(
            answer='当前 Agent 服务暂时不可用，请稍后重试。',
            route='error',
            safe=True,
            category='error',
            reason='',  # 不暴露异常细节，详情仅记日志
            sources=[],
            success=False,
            traceId=trace_id,
        )
        return JSONResponse(status_code=502, content=response.model_dump(mode='json'))


def _checkpoint_failure_response(trace_id: str, status_code: int) -> JSONResponse:
    """Checkpoint POSTGRES 模式的内部契约失败响应，不泄漏连接或 DSN 信息。"""
    response = AgentResponse(
        answer='执行快照上下文不可用，请稍后重试。',
        route='error',
        safe=True,
        category='error',
        reason='',
        sources=[],
        success=False,
        traceId=trace_id,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode='json'))


def _recovery_conflict_response(trace_id: str) -> JSONResponse:
    """Hide internal recovery reasons while exposing the stable 409 contract."""
    response = AgentResponse(
        answer='当前会话存在未完成的 Agent 执行，请重试原请求或重新开始会话。',
        route='error',
        safe=True,
        category='recovery_conflict',
        reason='',
        sources=[],
        success=False,
        traceId=trace_id,
    )
    return JSONResponse(status_code=409, content=response.model_dump(mode='json'))
