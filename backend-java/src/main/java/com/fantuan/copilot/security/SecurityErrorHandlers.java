package com.fantuan.copilot.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.dto.auth.AuthErrorResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Component
public class SecurityErrorHandlers {
    private static final String ADMIN_PATH = "/api/admin/";

    private final ObjectMapper objectMapper;
    private final AdminLogBuffer adminLogBuffer;

    public SecurityErrorHandlers(ObjectMapper objectMapper, AdminLogBuffer adminLogBuffer) {
        this.objectMapper = objectMapper;
        this.adminLogBuffer = adminLogBuffer;
    }

    public AuthenticationEntryPoint authenticationEntryPoint() {
        return (request, response, exception) -> write(response, request,
                HttpServletResponse.SC_UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "请先登录。");
    }

    public AccessDeniedHandler accessDeniedHandler() {
        return (request, response, exception) -> {
            recordAdminAccessDenied(request);
            write(response, request,
                    HttpServletResponse.SC_FORBIDDEN, "FORBIDDEN", "无权访问该资源。");
        };
    }

    private void recordAdminAccessDenied(HttpServletRequest request) {
        String path = request.getRequestURI();
        if (path == null || !path.startsWith(ADMIN_PATH)) {
            return;
        }
        Object traceIdAttr = request.getAttribute("traceId");
        String traceId = traceIdAttr == null ? "unknown" : traceIdAttr.toString();
        try {
            adminLogBuffer.record(
                    AdminLogEvent.LEVEL_WARN,
                    AdminLogEvent.CATEGORY_SECURITY,
                    "ADMIN_ACCESS_DENIED",
                    traceId,
                    null,
                    null,
                    null,
                    String.valueOf(HttpServletResponse.SC_FORBIDDEN),
                    null,
                    "Admin endpoint access denied");
        } catch (RuntimeException ignored) {
            // 日志记录失败不能阻断主响应
        }
    }

    private void write(HttpServletResponse response, HttpServletRequest request,
                       int status, String errorCode, String message) throws IOException {
        Object traceId = request.getAttribute("traceId");
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setHeader("Cache-Control", CacheControl.noStore().getHeaderValue());
        objectMapper.writeValue(response.getOutputStream(), new AuthErrorResponse(errorCode, message,
                traceId == null ? "unknown" : traceId.toString()));
    }
}
