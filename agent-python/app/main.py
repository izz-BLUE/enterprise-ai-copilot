import os
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.agents.langgraph_agent import (
    resume_external_langgraph_agent,
    resume_hitl_langgraph_agent,
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.agents.runtime_context import ExecutionMode
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
from app.integrations.mcp.enterprise_oa_client import get_enterprise_oa_client
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
from app.runtime.execution_recovery import (
    RecoveryMode,
    inspect_external_resume,
    inspect_hitl_resume,
)
from app.schemas.chat_schema import (
    AgentMemoryProposal,
    AgentResponse,
    ChatRequest,
    ChatResponse,
)
from app.schemas.expense_revalidation_schema import (
    ExpenseRevalidationInvoiceFact,
    ExpenseRevalidationRequest,
    ExpenseRevalidationResponse,
    ExpenseRevalidationTripFact,
)
from app.schemas.external_wait_schema import ExternalResumePayload
from app.schemas.hitl_schema import HitlResumePayload
from app.schemas.task_decomposition_schema import (
    TaskDecompositionRequest,
    TaskDecompositionResult,
)
from app.schemas.version_schema import VersionResponse
from app.services.llm_service import call_llm
from app.services.rag_service import process_chat
from app.services.task_decomposition_service import decompose_write_tasks


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

_BOUNDED_AI_PATHS = {
    '/agent/chat',
    '/agent/tasks/decompose',
    '/agent/langgraph/chat',
    '/agent/langgraph/hitl/resume',
    '/agent/langgraph/external/resume',
}

# 配置在 import 时 fail-closed 校验；模式不在白名单时服务不应静默运行。
_memory_execution_policy = make_execution_policy(MEMORY_WRITE_MODE)

_OA_REVALIDATION_BUSINESS_CODES = {
    'OA_MCP_INVOICE_NOT_FOUND',
    'OA_MCP_INVOICE_OWNERSHIP',
}


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
    if path in {
        '/agent/tasks/decompose',
        '/agent/langgraph/chat',
        '/agent/langgraph/hitl/resume',
        '/agent/langgraph/external/resume',
    }:
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


def _trusted_execution_mode(req: Request, trace_id: str,
                            task_id: str | None) -> tuple[ExecutionMode | None, JSONResponse | None]:
    raw = (req.headers.get('x-agent-execution-mode') or 'LEGACY_SINGLE').strip()
    if raw not in ('LEGACY_SINGLE', 'TASK_RUNTIME'):
        return None, _checkpoint_failure_response(trace_id, status_code=400)
    if raw == 'TASK_RUNTIME' and (task_id is None or not task_id.strip()):
        return None, _checkpoint_failure_response(trace_id, status_code=400)
    return raw, None


def _task_input_message(request: ChatRequest, execution_mode: ExecutionMode) -> str:
    if execution_mode != 'TASK_RUNTIME' or not request.clarificationContext:
        return request.message
    return f'{request.message}\n补充信息：{request.clarificationContext}'


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


def _revalidation_unavailable(trace_id: str) -> JSONResponse:
    """Keep transport failure retryable and hide MCP details from Java callers."""
    return JSONResponse(
        status_code=503,
        content=ExpenseRevalidationResponse(
            success=False,
            error_code='EXPENSE_REVALIDATION_UNAVAILABLE',
            message='Enterprise OA 当前不可用，请稍后重试。',
        ).model_dump(mode='json'),
        headers={'X-Trace-Id': trace_id, 'Retry-After': '1'},
    )


def _mcp_error_code(payload: dict) -> str:
    return str(payload.get('error_code') or 'OA_MCP_TOOL_ERROR')


def _trip_fact(raw: object) -> ExpenseRevalidationTripFact:
    if not isinstance(raw, dict):
        return ExpenseRevalidationTripFact()
    return ExpenseRevalidationTripFact(
        trip_id=raw.get('trip_id') if isinstance(raw.get('trip_id'), str) else None,
        employee_id=raw.get('employee_id') if isinstance(raw.get('employee_id'), str) else None,
        start_date=raw.get('start_date') if isinstance(raw.get('start_date'), str) else None,
        end_date=raw.get('end_date') if isinstance(raw.get('end_date'), str) else None,
        status=raw.get('status') if isinstance(raw.get('status'), str) else None,
    )


def _invoice_fact(raw: object) -> ExpenseRevalidationInvoiceFact:
    if not isinstance(raw, dict):
        return ExpenseRevalidationInvoiceFact()
    return ExpenseRevalidationInvoiceFact(
        invoice_id=raw.get('invoice_id') if isinstance(raw.get('invoice_id'), str) else None,
        valid=raw.get('valid') if isinstance(raw.get('valid'), bool) else None,
        duplicate=raw.get('duplicate') if isinstance(raw.get('duplicate'), bool) else None,
        amount=(Decimal(str(raw.get('amount')))
                if isinstance(raw.get('amount'), (int, float, Decimal))
                and not isinstance(raw.get('amount'), bool) else None),
        category=raw.get('category') if isinstance(raw.get('category'), str) else None,
        ownership_accepted=True if raw.get('success') is True else None,
        error_code=_mcp_error_code(raw) if raw.get('success') is not True else None,
    )


@app.post('/agent/internal/expense/revalidate', response_model=ExpenseRevalidationResponse)
def expense_revalidate(
    request: ExpenseRevalidationRequest,
    req: Request,
) -> ExpenseRevalidationResponse | JSONResponse:
    """Transport current OA facts for Java's confirm-time decision only.

    This endpoint deliberately bypasses Planner, LLM, LangGraph, Memory and
    Checkpoint.  Expected invoice business errors remain facts for Java to
    classify as stale; transport/protocol failures remain retryable 503s.
    """
    trace_id = req.state.trace_id
    client = get_enterprise_oa_client()
    try:
        trip_result = client.travel_record_get(
            employee_id=request.employee_id,
            limit=20,
        )
        if not isinstance(trip_result, dict) or trip_result.get('success') is not True:
            return _revalidation_unavailable(trace_id)
        trip_items = trip_result.get('items')
        if not isinstance(trip_items, list):
            return _revalidation_unavailable(trace_id)
        matching_trip = next(
            (item for item in trip_items
             if isinstance(item, dict) and item.get('trip_id') == request.trip_id),
            None,
        )

        invoice_facts: list[ExpenseRevalidationInvoiceFact] = []
        for invoice_id in request.invoice_ids:
            invoice_result = client.invoice_verify(
                invoice_id=invoice_id,
                employee_id=request.employee_id,
            )
            if not isinstance(invoice_result, dict):
                return _revalidation_unavailable(trace_id)
            if invoice_result.get('success') is not True:
                code = _mcp_error_code(invoice_result)
                if code not in _OA_REVALIDATION_BUSINESS_CODES:
                    return _revalidation_unavailable(trace_id)
            invoice_facts.append(_invoice_fact(invoice_result))
    except Exception:
        logger.exception('[%s] Enterprise OA confirm-time revalidation failed', trace_id)
        return _revalidation_unavailable(trace_id)

    return ExpenseRevalidationResponse(
        success=True,
        trip=_trip_fact(matching_trip) if matching_trip is not None else None,
        invoices=invoice_facts,
    )


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


@app.post('/agent/tasks/decompose', response_model=TaskDecompositionResult)
def decompose_tasks(request: TaskDecompositionRequest, req: Request) -> TaskDecompositionResult:
    """Stateless, deterministic decomposition; no checkpoint or lifecycle state."""
    trace_id = req.state.trace_id
    logger.info('[%s] 收到 Task decomposition 请求 (len=%d)', trace_id, len(request.message))
    return decompose_write_tasks(request.message)


@app.post('/agent/langgraph/chat', response_model=AgentResponse)
def langgraph_chat(request: ChatRequest, req: Request) -> AgentResponse | JSONResponse:
    trace_id = req.state.trace_id
    logger.info('[%s] 收到 LangGraph Agent 请求 (len=%d)', trace_id, len(request.message))

    execution_mode, mode_error = _trusted_execution_mode(req, trace_id, request.taskId)
    if mode_error is not None:
        return mode_error
    if execution_mode == 'TASK_RUNTIME' and LANGGRAPH_CHECKPOINT_MODE != 'POSTGRES':
        return _checkpoint_failure_response(trace_id, status_code=503)
    task_message = _task_input_message(request, execution_mode)

    if _validate_message_length(task_message, trace_id):
        logger.warning('[%s] 输入过长 (len=%d > %d)', trace_id,
                       len(task_message), MAX_MESSAGE_LENGTH)
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
    # 该值由 LangGraphAgentController 从已验证 JWT 身份解析后写入 header，
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
    skip_memory_pipeline = False
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
                            question=task_message,
                            business_date=business_date,
                            employee_id=employee_id,
                            allow_eval=allow_eval,
                            allow_business_actions=allow_business_actions,
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
                elif recovery is not None and recovery.mode is RecoveryMode.WAITING_USER:
                    # A normal chat must never cross an active approval
                    # interrupt.  Return the persisted proposal/wait marker so
                    # Java can perform idempotent PendingAction registration.
                    snapshot = graph.get_state(
                        {'configurable': {'thread_id': runtime_thread_id}},
                    )
                    values = getattr(snapshot, 'values', None)
                    if not isinstance(values, dict):
                        return _checkpoint_failure_response(trace_id, status_code=503)
                    result = dict(values)
                    result.update({
                        'route': 'action',
                        'category': 'business_action',
                        'safe': True,
                        'success': True,
                        'hitl_wait': recovery.hitl_wait,
                    })
                elif recovery is not None and recovery.mode is RecoveryMode.WAITING_EXTERNAL:
                    # An external OA wait outranks a new user question and is
                    # never crossed by ordinary crash recovery.
                    snapshot = graph.get_state(
                        {'configurable': {'thread_id': runtime_thread_id}},
                    )
                    values = getattr(snapshot, 'values', None)
                    if not isinstance(values, dict):
                        return _checkpoint_failure_response(trace_id, status_code=503)
                    result = dict(values)
                    result.update({
                        'answer': '报销申请已提交，正在等待外部审批。',
                        'route': 'action',
                        'category': 'business_action',
                        'safe': True,
                        'success': True,
                        'external_wait': recovery.external_wait,
                    })
                    skip_memory_pipeline = True
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
                        task_message,
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
                        execution_mode=execution_mode,
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
            if execution_mode == 'TASK_RUNTIME':
                agent_kwargs['execution_mode'] = execution_mode
            result = run_langgraph_agent(task_message, **agent_kwargs)

        # Memory 提案是出口层旁路；任何 Pipeline 失败都不得阻断主响应。
        # Python 不再反向调用 Java，不持有 owner 签名能力；Java 在当前已认证请求内落库。
        conversation_id = req.headers.get('x-conversation-id', '')
        memory_proposal = None
        if not skip_memory_pipeline and _memory_execution_policy.mode != 'DISABLED':
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
            hitl_wait=result.get('hitl_wait'),
            external_wait=result.get('external_wait'),
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


@app.post('/agent/langgraph/hitl/resume', response_model=AgentResponse)
def langgraph_hitl_resume(payload: HitlResumePayload, req: Request) -> AgentResponse | JSONResponse:
    """Resume one persisted business-action interrupt with Java authority.

    This endpoint has no browser contract.  The thread id and capability
    headers are accepted only from Java's internal gateway; the payload itself
    is still strictly validated and correlated with the latest checkpoint.
    Unlike normal chat, this path never runs the Memory proposal pipeline.
    """
    trace_id = req.state.trace_id
    execution_mode, mode_error = _trusted_execution_mode(
        req, trace_id, req.headers.get('x-agent-task-id'))
    if mode_error is not None:
        return mode_error
    if LANGGRAPH_CHECKPOINT_MODE != 'POSTGRES':
        return _checkpoint_failure_response(trace_id, status_code=503)

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
            return _hitl_resume_conflict_response(trace_id)
    employee_id = (req.headers.get('x-employee-id') or '').strip()

    checkpoint_runtime = getattr(app.state, 'checkpoint_runtime', None)
    if checkpoint_runtime is None:
        return _checkpoint_failure_response(trace_id, status_code=503)
    try:
        runtime_thread_id = checkpoint_runtime.build_thread_id(
            (req.headers.get('x-agent-thread-id') or '').strip(),
            use_planner=True,
        )
        graph = checkpoint_runtime.get_graph(use_planner=True)
    except (RuntimeError, ValueError):
        return _checkpoint_failure_response(trace_id, status_code=400)

    if not checkpoint_runtime.try_acquire_thread(runtime_thread_id):
        return _busy_response('/agent/langgraph/hitl/resume', trace_id)
    try:
        try:
            decision = inspect_hitl_resume(
                graph.get_state({'configurable': {'thread_id': runtime_thread_id}}),
                payload,
                employee_id=employee_id,
                allow_business_actions=allow_business_actions,
            )
        except Exception:
            logger.exception('[%s] HITL resume inspection 读取失败', trace_id)
            return _checkpoint_failure_response(trace_id, status_code=503)

        if decision.mode is RecoveryMode.WAITING_USER:
            result = resume_hitl_langgraph_agent(
                graph=graph,
                runtime_thread_id=runtime_thread_id,
                payload=payload,
                allow_eval=allow_eval,
                allow_business_actions=allow_business_actions,
                business_date=business_date,
                trace_id=trace_id,
                employee_id=employee_id,
                execution_mode=execution_mode,
            )
        elif decision.mode is RecoveryMode.HITL_CONTINUATION:
            # Approval has already been checkpointed; only deterministic
            # finalization is pending after a Python crash.
            result = resume_langgraph_agent(
                graph=graph,
                runtime_thread_id=runtime_thread_id,
                allow_eval=allow_eval,
                allow_business_actions=allow_business_actions,
                business_date=business_date,
                trace_id=trace_id,
                employee_id=employee_id,
                execution_mode=execution_mode,
            )
        elif decision.mode is RecoveryMode.HITL_COMPLETED:
            snapshot = graph.get_state(
                {'configurable': {'thread_id': runtime_thread_id}},
            )
            result = dict(snapshot.values)
        elif decision.mode is RecoveryMode.WAITING_EXTERNAL:
            # P3-4 response-loss recovery: the CONFIRMED decision was already
            # consumed and the second interrupt is durable.  Return it without
            # re-running approval, Planner, Tool, or any resume command.
            snapshot = graph.get_state(
                {'configurable': {'thread_id': runtime_thread_id}},
            )
            result = dict(snapshot.values)
        else:
            logger.warning(
                '[%s] HITL resume rejected mode=%s reason=%s',
                trace_id, decision.mode.value, decision.reason,
            )
            return _hitl_resume_conflict_response(trace_id)
    except Exception:
        logger.exception('[%s] HITL resume 执行失败', trace_id)
        return _checkpoint_failure_response(trace_id, status_code=502)
    finally:
        checkpoint_runtime.release_thread(runtime_thread_id)

    response = AgentResponse(
        answer=result.get('answer', ''),
        route=result.get('route', 'action'),
        safe=result.get('safe', True),
        category=result.get('category', 'business_action'),
        reason='',
        sources=result.get('sources', []),
        success=result.get('route', 'action') != 'error',
        traceId=trace_id,
        action_proposal=None,
        missing_fields=[],
        memory_proposal=None,
        hitl_wait=result.get('hitl_wait'),
        external_wait=result.get('external_wait'),
    )
    if not response.success:
        return JSONResponse(status_code=502, content=response.model_dump(mode='json'))
    return response


@app.post('/agent/langgraph/external/resume', response_model=AgentResponse)
def langgraph_external_resume(
    payload: ExternalResumePayload,
    req: Request,
) -> AgentResponse | JSONResponse:
    """Resume one durable external expense approval with Java authority."""
    trace_id = req.state.trace_id
    execution_mode, mode_error = _trusted_execution_mode(
        req, trace_id, req.headers.get('x-agent-task-id'))
    if mode_error is not None:
        return mode_error
    if execution_mode == 'TASK_RUNTIME':
        return _external_resume_conflict_response(trace_id)
    if LANGGRAPH_CHECKPOINT_MODE != 'POSTGRES':
        return _checkpoint_failure_response(trace_id, status_code=503)

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
            return _external_resume_conflict_response(trace_id)
    employee_id = (req.headers.get('x-employee-id') or '').strip()

    checkpoint_runtime = getattr(app.state, 'checkpoint_runtime', None)
    if checkpoint_runtime is None:
        return _checkpoint_failure_response(trace_id, status_code=503)
    try:
        runtime_thread_id = checkpoint_runtime.build_thread_id(
            (req.headers.get('x-agent-thread-id') or '').strip(),
            use_planner=True,
        )
        graph = checkpoint_runtime.get_graph(use_planner=True)
    except (RuntimeError, ValueError):
        return _checkpoint_failure_response(trace_id, status_code=400)

    if not checkpoint_runtime.try_acquire_thread(runtime_thread_id):
        return _busy_response('/agent/langgraph/external/resume', trace_id)
    try:
        try:
            decision = inspect_external_resume(
                graph.get_state({'configurable': {'thread_id': runtime_thread_id}}),
                payload,
                employee_id=employee_id,
            )
        except Exception:
            logger.exception('[%s] External resume inspection 读取失败', trace_id)
            return _checkpoint_failure_response(trace_id, status_code=503)

        if decision.mode is RecoveryMode.WAITING_EXTERNAL:
            result = resume_external_langgraph_agent(
                graph=graph,
                runtime_thread_id=runtime_thread_id,
                payload=payload,
                allow_eval=allow_eval,
                allow_business_actions=allow_business_actions,
                business_date=business_date,
                trace_id=trace_id,
                employee_id=employee_id,
                execution_mode=execution_mode,
            )
        elif decision.mode is RecoveryMode.EXTERNAL_CONTINUATION:
            result = resume_langgraph_agent(
                graph=graph,
                runtime_thread_id=runtime_thread_id,
                allow_eval=allow_eval,
                allow_business_actions=allow_business_actions,
                business_date=business_date,
                trace_id=trace_id,
                employee_id=employee_id,
                execution_mode=execution_mode,
            )
        elif decision.mode is RecoveryMode.EXTERNAL_COMPLETED:
            snapshot = graph.get_state(
                {'configurable': {'thread_id': runtime_thread_id}},
            )
            result = dict(snapshot.values)
        else:
            logger.warning(
                '[%s] External resume rejected mode=%s reason=%s',
                trace_id, decision.mode.value, decision.reason,
            )
            return _external_resume_conflict_response(trace_id)
    except Exception:
        logger.exception('[%s] External resume 执行失败', trace_id)
        return _checkpoint_failure_response(trace_id, status_code=502)
    finally:
        checkpoint_runtime.release_thread(runtime_thread_id)

    response = AgentResponse(
        answer=result.get('answer', ''),
        route=result.get('route', 'action'),
        safe=result.get('safe', True),
        category=result.get('category', 'business_action'),
        reason='',
        sources=result.get('sources', []),
        success=result.get('route', 'action') != 'error',
        traceId=trace_id,
        action_proposal=None,
        missing_fields=[],
        memory_proposal=None,
        hitl_wait=result.get('hitl_wait'),
        external_wait=result.get('external_wait'),
    )
    if not response.success:
        return JSONResponse(status_code=502, content=response.model_dump(mode='json'))
    return response


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


def _hitl_resume_conflict_response(trace_id: str) -> JSONResponse:
    response = AgentResponse(
        answer='当前确认请求与执行快照不匹配，未恢复该业务动作。',
        route='error',
        safe=True,
        category='recovery_conflict',
        reason='',
        sources=[],
        success=False,
        traceId=trace_id,
    )
    return JSONResponse(status_code=409, content=response.model_dump(mode='json'))


def _external_resume_conflict_response(trace_id: str) -> JSONResponse:
    response = AgentResponse(
        answer='当前外部审批结果与执行快照不匹配，未恢复该业务动作。',
        route='error',
        safe=True,
        category='recovery_conflict',
        reason='',
        sources=[],
        success=False,
        traceId=trace_id,
    )
    return JSONResponse(status_code=409, content=response.model_dump(mode='json'))
