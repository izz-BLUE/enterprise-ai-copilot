package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.gateway.python.PythonAgentBusyException;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.action.BusinessActionHitlCoordinator;
import com.fantuan.copilot.service.action.BusinessActionHandlerRegistry;
import com.fantuan.copilot.service.action.TaskRuntimeRegistrationRejectionException;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.agent.AgentEventRecorder;
import com.fantuan.copilot.service.agent.AgentMemoryCoordinator;
import com.fantuan.copilot.service.agent.AgentResponseFactory;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.task.TaskRuntimeException;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ResponseEntity;

import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;

@RestController
public class LangGraphAgentController {

    /** conversationId 合法字符集与 DTO 校验一致；用于服务端兜底生成后的二次校验。 */
    private static final Pattern CONVERSATION_ID_PATTERN =
            Pattern.compile("[A-Za-z0-9._\\-:]++");

    /**
     * 解析本次请求的 conversationId。
     * 优先级：请求体显式提供 → 服务端生成新的纯 UUID v4。
     * 永远不信任任何 header 形式的 conversationId（防客户端伪造）。
     * 返回值是已校验通过的会话 ID，会回传到 X-Conversation-Id 响应头与 X-Conversation-Id Python 请求头。
     *
     * 注意：不再把 trusted userId 编码进 conversationId（避免身份信息泄漏到会话标识中）。
     * 数据库复合 key 仍然由 (trusted user_id, conversation_id) 共同构成安全作用域。
     */
    static String resolveConversationId(String requested) {
        if (requested != null) {
            String trimmed = requested.trim();
            if (!trimmed.isEmpty()
                    && trimmed.length() <= 64
                    && CONVERSATION_ID_PATTERN.matcher(trimmed).matches()) {
                return trimmed;
            }
        }
        return UUID.randomUUID().toString();
    }

    /**
     * 基于服务端可信 (userId, conversationId) 复合 key 读取当前会话的 ACTIVE memory，
     * 构造内部 MemoryContextView。返回 empty 表示"无 Memory"。
     *
     * 安全/容错保证：
     *  1. 仅按 (userId, conversationId) 复合 key 读取；userId 来自服务端已认证 identity。
     *  2. 仅 status=ACTIVE 才返回 view；COMPLETED/ABANDONED/不存在 → empty。
     *  3. 读库异常 → 记录安全日志 + 返回 empty，绝不阻断 Agent 请求。
     *  4. 视图字段由 AiTaskMemoryService / DB CHECK 约束保证大小上限，无需在此重复校验。
     *  5. 视图不包含 userId / conversationId / nonce / idempotencyKey 等敏感或业务字段。
     */
    Optional<InternalAgentChatRequest.MemoryContextView> loadMemoryContext(
            String userId, String conversationId, String traceId) {
        try {
            return memoryCoordinator.load(userId, conversationId, traceId);
        } catch (RuntimeException exception) {
            return Optional.empty();
        }
    }

    private static final Logger log = LoggerFactory.getLogger(LangGraphAgentController.class);
    private final PythonAgentGateway pythonAgentGateway;
    private final AdminAccessService adminAccessService;
    private final BusinessActionService businessActionService;
    private final IdentityContext identityContext;
    private final AgentMemoryCoordinator memoryCoordinator;
    private final AgentEventRecorder eventRecorder;
    private final AgentRuntimeThreadIdService runtimeThreadIdService;
    private final AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard;
    private final BusinessActionHitlCoordinator hitlCoordinator;
    private final TaskRuntimeService taskRuntimeService;
    private final BusinessActionHandlerRegistry handlerRegistry;

    @Autowired
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer,
                                    AgentRuntimeThreadIdService runtimeThreadIdService,
                                    AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard,
                                    BusinessActionHitlCoordinator hitlCoordinator,
                                    TaskRuntimeService taskRuntimeService,
                                    BusinessActionHandlerRegistry handlerRegistry) {
        this.pythonAgentGateway = pythonAgentGateway;
        this.adminAccessService = adminAccessService;
        this.businessActionService = businessActionService;
        this.identityContext = identityContext;
        this.memoryCoordinator = new AgentMemoryCoordinator(memoryService);
        this.eventRecorder = new AgentEventRecorder(adminLogBuffer);
        this.runtimeThreadIdService = runtimeThreadIdService;
        this.runtimeThreadExecutionGuard = runtimeThreadExecutionGuard;
        this.hitlCoordinator = hitlCoordinator;
        this.taskRuntimeService = taskRuntimeService;
        this.handlerRegistry = handlerRegistry;
    }

    /**
     * 供既有无 Spring 上下文的 Controller 单元测试使用；生产路径一律走注入构造器。
     */
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer) {
        this(pythonAgentGateway, adminAccessService, businessActionService, identityContext,
                memoryService, adminLogBuffer, new AgentRuntimeThreadIdService(),
                new AgentRuntimeThreadExecutionGuard(), null, null);
    }

    /** 供只提供 guard、但不提供 HITL 的测试使用的生产形态构造方法。 */
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer,
                                    AgentRuntimeThreadIdService runtimeThreadIdService,
                                    AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard) {
        this(pythonAgentGateway, adminAccessService, businessActionService, identityContext,
                memoryService, adminLogBuffer, runtimeThreadIdService,
                runtimeThreadExecutionGuard, null, null);
    }

    /** 兼容提供现有 HITL coordinator 的测试的构造方法。 */
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer,
                                    AgentRuntimeThreadIdService runtimeThreadIdService,
                                    AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard,
                                    BusinessActionHitlCoordinator hitlCoordinator) {
        this(pythonAgentGateway, adminAccessService, businessActionService, identityContext,
                memoryService, adminLogBuffer, runtimeThreadIdService,
                runtimeThreadExecutionGuard, hitlCoordinator, null);
    }

    /** 供既有 Task Runtime 聚焦测试使用的构造方法。 */
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer,
                                    AgentRuntimeThreadIdService runtimeThreadIdService,
                                    AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard,
                                    BusinessActionHitlCoordinator hitlCoordinator,
                                    TaskRuntimeService taskRuntimeService) {
        this(pythonAgentGateway, adminAccessService, businessActionService, identityContext,
                memoryService, adminLogBuffer, runtimeThreadIdService,
                runtimeThreadExecutionGuard, hitlCoordinator, taskRuntimeService,
                new BusinessActionHandlerRegistry(java.util.List.of()));
    }

    /**
     * 判断本次浏览器请求是否允许 eval 路由。
     * 管理能力只由 Java 从已验证 JWT 恢复的 ADMIN 身份授权。
     */
    @PostMapping("/api/agent/langgraph/chat")
    public ResponseEntity<AgentChatResponse> langgraphChat(@Valid @RequestBody ChatRequest request,
                                                           HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        String presentedToken = httpRequest.getHeader("X-Admin-Token");
        long started = System.nanoTime();
        VerifiedIdentity identity;
        try {
            identity = identityContext.require(httpRequest);
        } catch (ActionException exception) {
            return AgentResponseFactory.identityFailure(traceId, exception);
        }
        eventRecorder.record(traceId, "AGENT_REQUEST_RECEIVED", null, started);
        boolean allowEval = adminAccessService.isAdminIdentity(identity);
        boolean allowBusinessActions = businessActionService != null
                && identity != null
                && identity.employeeId() != null
                && businessActionService.isAllowed(presentedToken, identity);

        // conversationId 由服务端权威解析：identity.userId() 是 trusted userId，
        // 客户端提供 conversationId 时仅作为分组 hint；缺失时服务端生成纯 UUID v4。
        // 数据库安全作用域仍由 (trusted user_id, conversation_id) 复合 key 保证。
        String conversationId = resolveConversationId(request.conversationId());
        // 仅由已验证 identity.userId() 与已解析 conversationId 计算；绝不读取或透传
        // 客户端 X-Agent-Thread-Id。该值只定位 LangGraph 执行快照，不承载业务权威。
        String runtimeThreadId = runtimeThreadIdService.generate(identity.userId(), conversationId);
        if (!runtimeThreadExecutionGuard.tryAcquire(runtimeThreadId)) {
            return AgentResponseFactory.busy(traceId);
        }

        try {
            return executeWithRuntimeThreadGuard(request, traceId, started,
                    presentedToken, identity, allowEval, allowBusinessActions,
                    conversationId, runtimeThreadId);
        } finally {
            runtimeThreadExecutionGuard.release(runtimeThreadId);
        }
    }

    private ResponseEntity<AgentChatResponse> executeWithRuntimeThreadGuard(
            ChatRequest request, String traceId, long started,
            String presentedToken, VerifiedIdentity identity, boolean allowEval,
            boolean allowBusinessActions, String conversationId, String runtimeThreadId) {

        // PendingAction TTL 由 Java 负责。在当前请求仍持有覆盖后续普通 Chat 调用的
        // 同一 runtime-thread guard 时，收口已过期的审批；coordinator 仅在 Java
        // 事务提交后执行 Python resume。
        if (hitlCoordinator != null) {
            try {
                if (!hitlCoordinator.reconcileExpiredBeforeChat(
                        traceId, presentedToken, identity, conversationId)) {
                    eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                            AdminLogEvent.LEVEL_WARN, started);
                    return AgentResponseFactory.recoveryConflict(traceId);
                }
            } catch (RuntimeException exception) {
                log.error("[{}] 过期 HITL 收口失败", traceId, exception);
                eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_ERROR, started);
                return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                        .body(AgentResponseFactory.fallback(traceId));
            }
        }

        // PendingAction 是 legacy single action 和 Task Runtime 共用的
        // WAITING_USER 权威边界。在 TTL reconciliation 之后、任何任务恢复或
        // 分解之前检查它。
        if (businessActionService != null && businessActionService.hasBlockingAction(
                identity.userId(), conversationId)) {
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_WARN, started);
            return AgentResponseFactory.actionFailure(traceId,
                    "当前会话已有待确认的申请，请先确认或取消后再发起新申请。");
        }

        // Task Runtime 的准入顺序由 Java 负责：活跃的用户等待或 clarification
        // 必须先绑定到对应任务，新消息才能进入分解。未构造 runtime service 的
        // legacy controller 保留原有单任务路径。
        TaskExecution taskExecution = null;
        if (taskRuntimeService != null) {
            try {
                // 该 Java 负责的 reconciliation 同时覆盖：上一次 Python 调用丢失的
                // RUNNING 任务，以及未阻断的 PENDING successor。它也保持准入顺序：
                // WAITING_USER -> WAITING_CLARIFICATION -> RUNNING 恢复
                // -> 确定性下一任务 -> 新的分解。
                taskExecution = taskRuntimeService.reconcile(
                        identity.userId(), conversationId).orElse(null);
                if (taskExecution != null
                        && taskExecution.status() == TaskExecutionStatus.WAITING_CLARIFICATION) {
                    taskExecution = taskRuntimeService.acceptClarification(
                            taskExecution, request.message()).orElse(taskExecution);
                } else if (taskExecution == null
                        && allowBusinessActions
                        && taskRuntimeService.isCompositeWriteCandidate(request.message())) {
                    taskExecution = taskRuntimeService.decomposeAndStart(
                            request.message(), identity.userId(), conversationId,
                            new HttpHeaders(), traceId);
                }
            } catch (TaskRuntimeException exception) {
                log.warn("[{}] Task Runtime admission rejected: {}", traceId, exception.getMessage());
                return AgentResponseFactory.actionFailure(traceId, exception.getMessage());
            }
        }

        String pythonThreadId = taskExecution == null
                ? runtimeThreadId
                : runtimeThreadIdService.generate(identity.userId(), conversationId,
                        taskExecution.taskId());
        String taskMessage = taskExecution == null
                ? request.message() : taskExecution.taskText();

        // Memory Read Path：服务端按 (userId, conversationId) 复合 key 读取 ai_task_memory，
        // 仅在 status=ACTIVE 时填充内部请求体的 memoryContext 字段。
        // memoryContext 不会出现在公共 ChatRequest 中（前端不可见 / 不可提交）。
        Optional<InternalAgentChatRequest.MemoryContextView> memoryContext = loadMemoryContext(
                identity.userId(), conversationId, traceId);

        log.info("[{}] 收到 LangGraph Agent 请求: allowEval={}, allowBusinessActions={}, conversationId={}, memoryAttached={}",
                traceId, allowEval, allowBusinessActions, conversationId,
                memoryContext.isPresent());

        // 构造内部请求体：message 来自公共 ChatRequest，memoryContext 由服务端权威填充。
        InternalAgentChatRequest internalBody = new InternalAgentChatRequest(
                taskMessage, memoryContext.orElse(null),
                taskExecution == null ? null : taskExecution.taskId(),
                taskExecution == null ? null : taskExecution.clarificationContext());

        PythonAgentResponse pythonResponse;
        PendingActionView pendingAction = null;
        boolean persistResponseMemory = true;
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Allow-Eval", String.valueOf(allowEval));
            headers.set("X-Allow-Business-Actions", String.valueOf(allowBusinessActions));
            if (businessActionService != null) {
                headers.set("X-Business-Date", businessActionService.businessDate().toString());
            }
            // 企业 Tool P0：把已通过身份校验的 employeeId 透传给 Python，供只读 Tool 使用。
            // 永远从服务端解析后的 identity 注入，不接受请求头直传，防止前端伪造身份。
            if (identity.employeeId() != null) {
                headers.set("X-Employee-Id", identity.employeeId());
            }
            // P0：透传服务端权威解析后的 conversationId 给 Python。
            headers.set("X-Conversation-Id", conversationId);
            headers.set("X-Agent-Thread-Id", pythonThreadId);
            headers.set("X-Agent-Execution-Mode",
                    taskExecution == null ? "LEGACY_SINGLE" : "TASK_RUNTIME");
            if (taskExecution != null) {
                headers.set("X-Agent-Task-Id", taskExecution.taskId());
            }
            pythonResponse = pythonAgentGateway.post(
                    "/agent/langgraph/chat", internalBody, headers,
                    PythonAgentResponse.class, traceId);
        } catch (PythonAgentBusyException exception) {
            return AgentResponseFactory.busy(traceId);
        } catch (PythonAgentTransportException exception) {
            log.error("[{}] Python 传输失败: status={}",
                    traceId, exception.responseStatus());
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            if (exception.responseStatus() == HttpStatus.CONFLICT) {
                return AgentResponseFactory.recoveryConflict(traceId);
            }
            return ResponseEntity.status(exception.responseStatus())
                    .body(AgentResponseFactory.fallback(traceId));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                    .body(AgentResponseFactory.fallback(traceId));
        }

        if (pythonResponse == null) {
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                    .body(AgentResponseFactory.fallback(traceId));
        }
        if (pythonResponse.hitlWait() != null && pythonResponse.actionProposal() == null) {
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_WARN, started);
            return AgentResponseFactory.actionFailure(traceId,
                    "HITL wait 缺少可确认的业务 Proposal。");
        }
        if (pythonResponse.hitlWait() != null
                && !pythonResponse.hitlWait().structurallyValid()) {
            eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_WARN, started);
            return AgentResponseFactory.actionFailure(traceId,
                    "HITL wait 上下文无效。");
        }
        boolean expenseReasonClarification = isExpenseReasonClarification(
                pythonResponse, request.message());
        if (pythonResponse.actionProposal() != null) {
            // 持久化 HITL wait 可能属于已经终态化的 Java action，而其 checkpoint
            // 仍需要 reconciliation。即使 capability 已撤销，也让 coordinator
            // 检查该 correlation；非 HITL Proposal 继续使用原有 gate。
            if (!allowBusinessActions && pythonResponse.hitlWait() == null) {
                eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_WARN, started);
                return AgentResponseFactory.actionFailure(traceId, "业务动作功能未启用或当前请求无权限。");
            }
            try {
                HitlWaitMarker wait = pythonResponse.hitlWait();
                if (taskExecution != null && !taskRuntimeService.matchesTaskType(
                        taskExecution, taskType(pythonResponse.actionProposal().actionType()))) {
                    throw new TaskRuntimeException("Task Runtime Proposal 与任务类型不匹配。");
                }
                if (wait != null && hitlCoordinator != null) {
                    pendingAction = hitlCoordinator.registerWait(
                            pythonResponse.actionProposal(), wait, traceId, presentedToken,
                            identity, conversationId,
                            taskExecution == null ? null : taskExecution.taskId());
                } else if (wait != null) {
                    pendingAction = businessActionService.createHitlPending(
                            pythonResponse.actionProposal(), traceId, presentedToken,
                            identity, conversationId,
                            wait.executionId(), wait.waitId());
                } else {
                        pendingAction = businessActionService.createPending(
                            pythonResponse.actionProposal(), traceId, presentedToken,
                        identity, conversationId);
                }
                if (taskExecution != null) {
                    if (pendingAction == null || !taskRuntimeService.markWaitingUser(
                            taskExecution.taskId(), pendingAction.actionId())) {
                        throw new TaskRuntimeException("Task Runtime PendingAction 关联失败。");
                    }
                    memoryCoordinator.persist(pythonResponse.memoryProposal(), identity.userId(),
                            conversationId, traceId);
                    persistResponseMemory = false;
                }
            } catch (TaskRuntimeRegistrationRejectionException exception) {
                log.warn("[{}] Task Runtime task rejected; successor pending action={}",
                        traceId, exception.successorPendingAction() == null
                                ? "none" : "present");
                eventRecorder.record(traceId, "AGENT_REQUEST_CONTINUED_AFTER_REJECTION",
                        AdminLogEvent.LEVEL_WARN, started);
                if (exception.successorPendingAction() == null) {
                    return AgentResponseFactory.actionFailure(traceId,
                            "暂时无法生成申请草稿，请检查信息后重试。");
                }
                return AgentResponseFactory.actionRejectedWithContinuation(
                        traceId,
                        "上一项申请因业务规则未能生成，已继续处理下一项任务，请确认。",
                        exception.successorPendingAction(), conversationId);
            } catch (ActionException exception) {
                log.warn("[{}] Python Proposal未创建 PendingAction: code={}",
                        traceId, exception.errorCode());
                if ("ACTION_CONVERSATION_IN_PROGRESS".equals(exception.errorCode())) {
                    eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                            AdminLogEvent.LEVEL_WARN, started);
                    return AgentResponseFactory.actionFailure(traceId,
                            "当前会话已有待确认的申请，请先确认或取消后再发起新申请。");
                }
                eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_WARN, started);
                return AgentResponseFactory.actionFailure(traceId, "暂时无法生成申请草稿，请检查信息后重试。");
            } catch (RuntimeException exception) {
                log.error("[{}] PendingAction持久化失败", traceId);
                eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_ERROR, started);
                return AgentResponseFactory.actionFailure(traceId, "业务动作处理失败，请稍后重试。");
            }
        }
        if (taskExecution != null && pendingAction == null
                && pythonResponse.missingFields() != null
                && !pythonResponse.missingFields().isEmpty()
                && !taskRuntimeService.markWaitingClarification(taskExecution.taskId())) {
            return AgentResponseFactory.actionFailure(traceId, "Task Runtime clarification 状态冲突。");
        }
        if (taskExecution != null && pendingAction == null
                && pythonResponse.missingFields() != null
                && !pythonResponse.missingFields().isEmpty()) {
            if (!expenseReasonClarification) {
                memoryCoordinator.persist(pythonResponse.memoryProposal(), identity.userId(),
                        conversationId, traceId);
            }
            persistResponseMemory = false;
        }
        if (taskExecution != null && pendingAction == null
                && (pythonResponse.missingFields() == null
                || pythonResponse.missingFields().isEmpty())) {
            if (!taskRuntimeService.markTerminal(taskExecution.taskId(),
                    TaskExecutionStatus.FAILED)) {
                return AgentResponseFactory.actionFailure(traceId,
                        "Task Runtime 未产生可执行的业务动作。");
            }
            memoryCoordinator.abandon(identity.userId(), conversationId, traceId);
            if (hitlCoordinator != null) {
                pendingAction = hitlCoordinator.startNextTaskAfterTerminal(taskExecution,
                        identity, presentedToken, traceId);
            }
            persistResponseMemory = false;
        }
        if (expenseReasonClarification) {
            memoryCoordinator.persistExpenseReasonContinuation(request.message(),
                    identity.userId(), conversationId, traceId);
            persistResponseMemory = false;
        }
        if (persistResponseMemory) {
            memoryCoordinator.persist(pythonResponse.memoryProposal(), identity.userId(),
                    conversationId, traceId);
        }
        eventRecorder.record(traceId, "AGENT_REQUEST_COMPLETED",
                AdminLogEvent.LEVEL_INFO, started);
        AgentChatResponse publicResponse = AgentChatResponse.fromPython(pythonResponse, pendingAction);
        ResponseEntity.BodyBuilder builder = ResponseEntity.ok();
        builder.header("X-Conversation-Id", conversationId);
        if (pendingAction != null) {
            builder.cacheControl(org.springframework.http.CacheControl.noStore());
        }
        return builder.body(publicResponse);
    }

    private boolean isExpenseReasonClarification(PythonAgentResponse response,
                                                  String requestMessage) {
        return isExpenseRequest(requestMessage)
                && "action".equals(response.route())
                && "business_action".equals(response.category())
                && response.actionProposal() == null
                && response.missingFields() != null
                && response.missingFields().equals(java.util.List.of("reason"));
    }

    private boolean isExpenseRequest(String message) {
        return message != null && (message.contains("报销") || message.contains("报账"));
    }

    private com.fantuan.copilot.model.task.TaskType taskType(
            com.fantuan.copilot.model.action.BusinessActionType actionType) {
        return handlerRegistry.taskTypeFor(actionType)
                .orElseThrow(() -> new TaskRuntimeException(
                        "Task Runtime Proposal action type 不受支持。"));
    }

}
