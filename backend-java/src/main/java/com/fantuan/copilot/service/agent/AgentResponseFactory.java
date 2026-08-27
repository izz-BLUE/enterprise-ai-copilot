package com.fantuan.copilot.service.agent;

import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.service.action.ActionException;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.List;

/** 统一构造 Agent 边界上的安全错误响应。 */
public final class AgentResponseFactory {
    private AgentResponseFactory() {}

    public static ResponseEntity<AgentChatResponse> busy(String traceId) {
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, "1")
                .body(response("当前请求较多，请稍后重试。", "busy", "overloaded", traceId));
    }

    public static AgentChatResponse fallback(String traceId) {
        return response("当前 Agent 服务暂时不可用，请稍后重试。", "error", "error", traceId);
    }

    public static ResponseEntity<AgentChatResponse> actionFailure(String traceId, String message) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(response(message, "error", "business_action", traceId));
    }

    public static ResponseEntity<AgentChatResponse> recoveryConflict(String traceId) {
        return ResponseEntity.status(HttpStatus.CONFLICT).cacheControl(CacheControl.noStore())
                .body(new AgentChatResponse(
                        "当前会话存在未完成的 Agent 执行，请重试原请求或重新开始会话。",
                        "error", true, "recovery_conflict", "", List.of(), false, traceId));
    }

    public static ResponseEntity<AgentChatResponse> identityFailure(
            String traceId, ActionException exception) {
        String category = exception.errorCode().startsWith("DEMO_")
                ? "demo_identity" : "authentication";
        return ResponseEntity.status(exception.httpStatus()).cacheControl(CacheControl.noStore())
                .body(response(exception.getMessage(), "error", category, traceId));
    }

    private static AgentChatResponse response(String answer, String route,
                                              String category, String traceId) {
        return new AgentChatResponse(answer, route, true, category,
                "", List.of(), false, traceId);
    }
}
