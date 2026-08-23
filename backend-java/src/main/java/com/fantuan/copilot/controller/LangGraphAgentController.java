package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.memory.MemoryWriteScopeService;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import java.util.List;
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
            Optional<AiTaskMemory> opt = memoryService.find(userId, conversationId);
            if (opt.isEmpty()) {
                return Optional.empty();
            }
            AiTaskMemory memory = opt.get();
            if (memory.status() != com.fantuan.copilot.model.memory.TaskStatus.ACTIVE) {
                return Optional.empty();
            }
            return Optional.of(new InternalAgentChatRequest.MemoryContextView(
                    memory.taskType(),
                    memory.status().name(),
                    memory.taskStateJson(),
                    memory.summary()));
        } catch (RuntimeException ex) {
            // 任何运行时异常（读库、连接池等）：按"无 Memory"继续，绝不阻断 Agent 请求。
            log.warn("[{}] memory context 读取失败: {}", traceId, ex.getMessage());
            return Optional.empty();
        }
    }

    private static final Logger log = LoggerFactory.getLogger(LangGraphAgentController.class);
    private final RestTemplate restTemplate;
    private final PythonAgentBulkhead pythonAgentBulkhead;
    private final AdminAccessService adminAccessService;
    private final BusinessActionService businessActionService;
    private final IdentityContext identityContext;
    private final AiTaskMemoryService memoryService;
    private final MemoryWriteScopeService memoryWriteScopeService;
    private final AdminLogBuffer adminLogBuffer;

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    @Autowired
    public LangGraphAgentController(RestTemplate restTemplate,
                                    PythonAgentBulkhead pythonAgentBulkhead,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService,
                                    IdentityContext identityContext,
                                    AiTaskMemoryService memoryService,
                                    MemoryWriteScopeService memoryWriteScopeService,
                                    AdminLogBuffer adminLogBuffer) {
        this.restTemplate = restTemplate;
        this.pythonAgentBulkhead = pythonAgentBulkhead;
        this.adminAccessService = adminAccessService;
        this.businessActionService = businessActionService;
        this.identityContext = identityContext;
        this.memoryService = memoryService;
        this.memoryWriteScopeService = memoryWriteScopeService;
        this.adminLogBuffer = adminLogBuffer;
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
            return safeIdentityFailure(traceId, exception);
        }
        recordAgentEvent(traceId, identity, "AGENT_REQUEST_RECEIVED", null, started);
        boolean allowEval = adminAccessService.isAdmin(presentedToken);
        boolean allowBusinessActions = businessActionService != null
                && identity != null
                && identity.employeeId() != null
                && businessActionService.isAllowed(presentedToken);

        // conversationId 由服务端权威解析：identity.userId() 是 trusted userId，
        // 客户端提供 conversationId 时仅作为分组 hint；缺失时服务端生成纯 UUID v4。
        // 数据库安全作用域仍由 (trusted user_id, conversation_id) 复合 key 保证。
        String conversationId = resolveConversationId(request.conversationId());

        // Memory Read Path：服务端按 (userId, conversationId) 复合 key 读取 ai_task_memory，
        // 仅在 status=ACTIVE 时填充内部请求体的 memoryContext 字段。
        // memoryContext 不会出现在公共 ChatRequest 中（前端不可见 / 不可提交）。
        Optional<InternalAgentChatRequest.MemoryContextView> memoryContext = loadMemoryContext(
                identity.userId(), conversationId, traceId);

        log.info("[{}] 收到 LangGraph Agent 请求: allowEval={}, allowBusinessActions={}, conversationId={}, memoryAttached={}",
                traceId, allowEval, allowBusinessActions, conversationId,
                memoryContext.isPresent());

        PythonAgentBulkhead.Permit permit = pythonAgentBulkhead.tryAcquire(traceId);
        if (permit == null) {
            return busy(traceId);
        }

        // 构造内部请求体：message 来自公共 ChatRequest，memoryContext 由服务端权威填充。
        InternalAgentChatRequest internalBody = new InternalAgentChatRequest(
                request.message(), memoryContext.orElse(null));

        PythonAgentResponse pythonResponse;
        try (permit) {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
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
            // Memory write scope 由 Java 基于 verified identity 签发；Python 只透传，
            // Java Memory endpoint 会重新验签并绑定 (userId, conversationId)。
            String memoryWriteScope = memoryWriteScopeService.issue(
                    identity.userId(), conversationId);
            if (memoryWriteScope != null) {
                headers.set("X-Memory-Write-Scope", memoryWriteScope);
            }
            HttpEntity<InternalAgentChatRequest> httpEntity = new HttpEntity<>(internalBody, headers);

            String url = agentBaseUrl + "/agent/langgraph/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<PythonAgentResponse> response = restTemplate.postForEntity(
                url,
                httpEntity,
                PythonAgentResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            pythonResponse = response.getBody();
            recordAgentEvent(traceId, identity, "AGENT_REQUEST_COMPLETED",
                    AdminLogEvent.LEVEL_INFO, started);
        } catch (HttpClientErrorException e) {
            if (e.getStatusCode().value() == HttpStatus.TOO_MANY_REQUESTS.value()) {
                log.warn("[{}] Python 并发已满", traceId);
                recordAgentEvent(traceId, identity, "AGENT_REQUEST_FAILED",
                        AdminLogEvent.LEVEL_WARN, started);
                return busy(traceId);
            }
            log.error("[{}] Python 返回 HTTP 4xx: status={}", traceId, e.getStatusCode());
            recordAgentEvent(traceId, identity, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            return ResponseEntity.ok(fallback(traceId));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            recordAgentEvent(traceId, identity, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            return ResponseEntity.ok(fallback(traceId));
        }

        if (pythonResponse == null) {
            recordAgentEvent(traceId, identity, "AGENT_REQUEST_FAILED",
                    AdminLogEvent.LEVEL_ERROR, started);
            return ResponseEntity.ok(fallback(traceId));
        }
        PendingActionView pendingAction = null;
        if (pythonResponse.actionProposal() != null) {
            if (!allowBusinessActions) {
                // Python 在响应出口已写入 Memory；动作链路不可用时收口为 ABANDONED，
                // 避免"动作未建立但 Memory 持续提示任务进行中"。
                memoryService.abandon(identity.userId(), conversationId);
                return safeActionFailure(traceId, "业务动作功能未启用或当前请求无权限。");
            }
            try {
                pendingAction = businessActionService.createPending(
                        pythonResponse.actionProposal(), traceId, presentedToken,
                        identity.asDemoIdentity(), conversationId);
            } catch (ActionException exception) {
                log.warn("[{}] Python Proposal未创建 PendingAction: code={}",
                        traceId, exception.errorCode());
                if ("ACTION_CONVERSATION_IN_PROGRESS".equals(exception.errorCode())) {
                    // 同会话已有活动动作：Memory 属于既有动作，不能收口为 ABANDONED，
                    // 否则既有动作确认时无法从 ACTIVE 转到 COMPLETED。
                    return safeActionFailure(traceId,
                            "当前会话已有待确认的申请，请先确认或取消后再发起新申请。");
                }
                memoryService.abandon(identity.userId(), conversationId);
                return safeActionFailure(traceId, "暂时无法生成申请草稿，请检查信息后重试。");
            } catch (RuntimeException exception) {
                log.error("[{}] PendingAction持久化失败", traceId);
                memoryService.abandon(identity.userId(), conversationId);
                return safeActionFailure(traceId, "业务动作处理失败，请稍后重试。");
            }
        }
        AgentChatResponse publicResponse = AgentChatResponse.fromPython(pythonResponse, pendingAction);
        ResponseEntity.BodyBuilder builder = ResponseEntity.ok();
        builder.header("X-Conversation-Id", conversationId);
        if (pendingAction != null) {
            builder.cacheControl(org.springframework.http.CacheControl.noStore());
        }
        return builder.body(publicResponse);
    }

    private ResponseEntity<AgentChatResponse> busy(String traceId) {
        AgentChatResponse response = new AgentChatResponse(
                "当前请求较多，请稍后重试。",
                "busy",
                true,
                "overloaded",
                "",
                List.of(),
                false,
                traceId
        );
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, "1")
                .body(response);
    }

    private AgentChatResponse fallback(String traceId) {
        return new AgentChatResponse(
                "当前 Agent 服务暂时不可用，请稍后重试。",
                "error",
                true,
                "error",
                "",  // reason 不暴露异常细节
                List.of(),
                false,
                traceId);
    }

    private ResponseEntity<AgentChatResponse> safeActionFailure(String traceId, String message) {
        AgentChatResponse response = new AgentChatResponse(message, "error", true,
                "business_action", "", List.of(), false, traceId);
        return ResponseEntity.ok()
                .cacheControl(org.springframework.http.CacheControl.noStore())
                .body(response);
    }

    private ResponseEntity<AgentChatResponse> safeIdentityFailure(String traceId,
                                                                  ActionException exception) {
        String category = exception.errorCode().startsWith("DEMO_")
                ? "demo_identity" : "authentication";
        AgentChatResponse response = new AgentChatResponse(exception.getMessage(), "error", true,
                category, "", List.of(), false, traceId);
        return ResponseEntity.status(exception.httpStatus())
                .cacheControl(org.springframework.http.CacheControl.noStore())
                .body(response);
    }

    private void recordAgentEvent(String traceId, VerifiedIdentity identity, String eventName,
                                  String level, long started) {
        try {
            // 第一版不记录 userRef：避免 hashCode 低熵碰撞与可枚举；
            // 排查依赖 traceId / category / event / durationMs 已足够。
            String safeLevel = level == null ? AdminLogEvent.LEVEL_INFO : level;
            long elapsed = (System.nanoTime() - started) / 1_000_000L;
            adminLogBuffer.record(
                    safeLevel,
                    AdminLogEvent.CATEGORY_AGENT,
                    eventName,
                    traceId,
                    null,
                    null,
                    null,
                    null,
                    elapsed,
                    safeAgentMessage(eventName));
        } catch (RuntimeException ignored) {
            // 日志记录失败不能影响主响应
        }
    }

    private static String safeAgentMessage(String eventName) {
        return switch (eventName) {
            case "AGENT_REQUEST_RECEIVED" -> "LangGraph Agent request received";
            case "AGENT_REQUEST_COMPLETED" -> "LangGraph Agent request completed";
            case "AGENT_REQUEST_FAILED" -> "LangGraph Agent request failed";
            default -> "LangGraph Agent event";
        };
    }
}
