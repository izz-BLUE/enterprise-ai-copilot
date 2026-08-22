package com.fantuan.copilot.controller.memory;

import com.fantuan.copilot.dto.memory.InternalMemoryWriteRequest;
import com.fantuan.copilot.dto.memory.MemoryWriteResponse;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.memory.MemoryWriteException;
import com.fantuan.copilot.service.memory.MemoryWriteScopeService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.regex.Pattern;

/**
 * Scoped Conversation Memory / Task Continuity P0 — Phase 4B Write API。
 *
 * 职责：接收 Python Agent (MemoryWriteDispatcher) 的写入请求，
 *       走 trusted (userId, conversationId) 复合 key 持久化。
 *
 * 身份边界（最重要 invariant）：
 *  1. userId 来自 Java LangGraph 请求签发的短时、HMAC 绑定 scope。
 *  2. conversationId 来自 URL path，并必须与 scope 中的 conversationId 一致。
 *  3. body 不接受任何身份字段；Python 只透传 scope，不参与 owner 决定。
 *  4. 服务间鉴权复用既有 X-Internal-Token / JAVA_INTERNAL_TOKEN；不使用用户 JWT 或 Admin Token。
 *
 * Endpoint: POST /api/internal/memory/conversations/{conversationId}/write
 */
@RestController
@RequestMapping("/api/internal/memory")
public class MemoryWriteController {

    private static final Logger log = LoggerFactory.getLogger(MemoryWriteController.class);

    /** 与 LangGraphAgentController.CONVERSATION_ID_PATTERN 对齐。 */
    private static final Pattern CONVERSATION_ID_PATTERN =
            Pattern.compile("[A-Za-z0-9._\\-:]++");

    private final AiTaskMemoryService memoryService;
    private final MemoryWriteScopeService scopeService;

    @org.springframework.beans.factory.annotation.Autowired
    public MemoryWriteController(AiTaskMemoryService memoryService,
                                 MemoryWriteScopeService scopeService) {
        this.memoryService = memoryService;
        this.scopeService = scopeService;
    }

    @PostMapping("/conversations/{conversationId}/write")
    public ResponseEntity<MemoryWriteResponse> write(
            @PathVariable("conversationId") String conversationId,
            @RequestHeader(value = "X-Internal-Token", required = false) String internalToken,
            @RequestHeader(value = "X-Memory-Write-Scope", required = false) String scopeToken,
            @Valid @RequestBody InternalMemoryWriteRequest request,
            HttpServletRequest httpRequest) {

        String traceId = (String) httpRequest.getAttribute("traceId");

        // 1. Service-to-service token：复用现有 Python → Java 内部只读链路凭证。
        if (!scopeService.matchesInternalToken(internalToken)) {
            log.warn("[{}] Memory write 拒绝: internal token 校验失败", traceId);
            throw new MemoryWriteException(HttpStatus.FORBIDDEN,
                    "MEMORY_INTERNAL_TOKEN_REQUIRED", "Memory write 服务间凭证无效或接口未启用。");
        }

        // 2. Scope 校验 —— userId 一定来自 Java 签发的可信上下文。
        MemoryWriteScopeService.Scope scope;
        try {
            scope = scopeService.verify(scopeToken);
        } catch (IllegalArgumentException e) {
            log.warn("[{}] Memory write 拒绝: scope 校验失败", traceId);
            throw new MemoryWriteException(HttpStatus.FORBIDDEN,
                    "MEMORY_SCOPE_INVALID", "Memory write trusted scope 无效或已过期。");
        }

        // 3. conversationId 必须同时满足 path 格式与 Java 签发 scope 绑定。
        String safeConversationId = sanitizeConversationId(conversationId);
        if (!safeConversationId.equals(scope.conversationId())) {
            log.warn("[{}] Memory write 拒绝: conversation scope 不匹配", traceId);
            throw new MemoryWriteException(HttpStatus.FORBIDDEN,
                    "MEMORY_SCOPE_MISMATCH", "conversationId 与 trusted scope 不匹配。");
        }

        log.info("[{}] Memory write 入口: userId={} conversationId={} action={} taskType={} status={}",
                traceId, scope.userId(), safeConversationId,
                request.action(), request.taskType(), request.status());

        // 4. 委托 Service 写入
        AiTaskMemory saved;
        try {
            saved = memoryService.writeFromCommand(
                    scope.userId(),
                    safeConversationId,
                    request.action(),
                    request.taskType(),
                    request.status(),
                    request.taskState(),
                    request.summary());
        } catch (IllegalArgumentException e) {
            // trusted-key 拒绝 / payload 校验失败 / 状态不匹配
            // 兜底：优先识别 trusted-key 错误码
            String msg = e.getMessage() == null ? "" : e.getMessage();
            if (msg.contains("trusted") || msg.contains("trusted key") || msg.contains("trusted 字段")) {
                log.warn("[{}] Memory write 拒绝: {}", traceId, msg);
                throw new MemoryWriteException(HttpStatus.BAD_REQUEST,
                        "MEMORY_TRUSTED_KEY_REJECTED", msg);
            }
            log.warn("[{}] Memory write 拒绝: {}", traceId, msg);
            throw new MemoryWriteException(HttpStatus.BAD_REQUEST,
                    "MEMORY_PAYLOAD_INVALID", msg);
        }

        TaskStatus savedStatus = saved.status();
        MemoryWriteResponse body = new MemoryWriteResponse(
                request.action(),
                saved.taskType(),
                savedStatus == null ? null : savedStatus.name(),
                saved.updatedAt());

        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }

    /**
     * Compatibility overload for direct controller tests/callers. Real HTTP
     * binding uses the annotated method above; the scope remains request-header
     * data and is never inferred from the body or the legacy second argument.
     */
    public ResponseEntity<MemoryWriteResponse> write(
            String conversationId,
            String internalToken,
            InternalMemoryWriteRequest request,
            HttpServletRequest httpRequest) {
        return write(conversationId, internalToken,
                httpRequest.getHeader("X-Memory-Write-Scope"), request, httpRequest);
    }

    private static String sanitizeConversationId(String raw) {
        if (raw == null) {
            throw new MemoryWriteException(HttpStatus.BAD_REQUEST,
                    "MEMORY_CONVERSATION_ID_INVALID", "conversationId 不能为空");
        }
        String trimmed = raw.trim();
        if (trimmed.isEmpty()
                || trimmed.length() > 64
                || !CONVERSATION_ID_PATTERN.matcher(trimmed).matches()) {
            throw new MemoryWriteException(HttpStatus.BAD_REQUEST,
                    "MEMORY_CONVERSATION_ID_INVALID",
                    "conversationId 格式非法：仅允许字母数字 / . _ - : 且不超过 64 字符");
        }
        return trimmed;
    }
}
