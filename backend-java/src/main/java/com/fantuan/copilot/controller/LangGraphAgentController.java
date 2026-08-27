package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.python.PythonAgentBusyException;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.agent.AgentEventRecorder;
import com.fantuan.copilot.service.agent.AgentMemoryCoordinator;
import com.fantuan.copilot.service.agent.AgentResponseFactory;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
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

    @Autowired
    public LangGraphAgentController(PythonAgentGateway pythonAgentGateway,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    AdminLogBuffer adminLogBuffer,
                                    AgentRuntimeThreadIdService runtimeThreadIdService,
                                    AgentRuntimeThreadExecutionGuard runtimeThreadExecutionGuard) {
        this.pythonAgentGateway = pythonAgentGateway;
        this.adminAccessService = adminAccessService;
        this.businessActionService = businessActionService;
        this.identityContext = identityContext;
        this.memoryCoordinator = new AgentMemoryCoordinator(memoryService);
        this.eventRecorder = new AgentEventRecorder(adminLogBuffer);
        this.runtimeThreadIdService = runtimeThreadIdService;
        this.runtimeThreadExecutionGuard = runtimeThreadExecutionGuard;
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
                new AgentRuntimeThreadExecutionGuard());
    }

    /**
     * 判断本次请求是否允许 eval 路由。
     *
     * 规则：
     * - admin.token 为空 → Demo 模式，允许 eval（不代表真实管理员认证）
     * - admin.token 非空且 X-Admin-Token 匹配 → 允许 eval
     * - admin.token 非空且 X-Admin-Token 缺失/不匹配 → 不允许 eval
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
        boolean allowEval = adminAccessService.isAdmin(presentedToken);
        boolean allowBusinessActions = businessActionService != null
                && identity != null
                && identity.employeeId() != null
                && businessActionService.isAllowed(presentedToken);

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
                request.message(), memoryContext.orElse(null));

        PythonAgentResponse pythonResponse;
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
            headers.set("X-Agent-Thread-Id", runtimeThreadId);
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
        PendingActionView pendingAction = null;
        if (pythonResponse.actionProposal() != null) {
            if (!allowBusinessActions) {
                eventRecorder.record(traceId, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_WARN, started);
                return AgentResponseFactory.actionFailure(traceId, "业务动作功能未启用或当前请求无权限。");
            }
            try {
                pendingAction = businessActionService.createPending(
                        pythonResponse.actionProposal(), traceId, presentedToken,
                        identity.asDemoIdentity(), conversationId);
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
        memoryCoordinator.persist(pythonResponse.memoryProposal(), identity.userId(),
                conversationId, traceId);
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

}
