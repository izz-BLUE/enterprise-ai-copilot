package com.fantuan.copilot.filter;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

/**
 * traceId 全链路过滤器。
 *
 * 策略：服务端统一生成 UUID traceId，不信任客户端传入的 X-Trace-Id。
 * traceId 仅用于链路追踪，不用于权限判断。
 *
 * 顺便负责三类轻量事件写入 AdminLogBuffer：
 *   1. REQUEST  - /api/agent/**、/api/internal/memory/** 完成
 *   2. SECURITY - 已认证非 ADMIN 用户访问 /api/admin/** 被拒
 *   3. 不记录静态资源、健康检查、admin 日志控制台自身的查询
 */
@Component
public class TraceIdFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(TraceIdFilter.class);
    private static final String HEADER = "X-Trace-Id";
    private static final String MDC_KEY = "traceId";
    private static final String ADMIN_PATH = "/api/admin/";
    private static final String AGENT_PATH = "/api/agent/";
    private static final String INTERNAL_MEMORY_PATH = "/api/internal/memory/";

    private final AdminLogBuffer adminLogBuffer;

    public TraceIdFilter(AdminLogBuffer adminLogBuffer) {
        this.adminLogBuffer = adminLogBuffer;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        String traceId = resolveTraceId(request, path);

        MDC.put(MDC_KEY, traceId);
        request.setAttribute(MDC_KEY, traceId);
        response.setHeader(HEADER, traceId);

        long started = System.nanoTime();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long durationMs = (System.nanoTime() - started) / 1_000_000L;
            recordRequestOutcome(request, response, traceId, path, durationMs);
            MDC.remove(MDC_KEY);
        }
    }

    private String resolveTraceId(HttpServletRequest request, String path) {
        // /api/internal/** 为可信内部调用（Python Agent → Java），透传上游 traceId。
        if (path.startsWith("/api/internal/")) {
            String upstream = request.getHeader(HEADER);
            if (upstream != null && !upstream.isBlank()) {
                return upstream;
            }
        }
        return UUID.randomUUID().toString();
    }

    private void recordRequestOutcome(HttpServletRequest request,
                                      HttpServletResponse response,
                                      String traceId,
                                      String path,
                                      long durationMs) {
        try {
            int status = response.getStatus();
            boolean isAgent = path.startsWith(AGENT_PATH);
            boolean isInternalMemory = path.startsWith(INTERNAL_MEMORY_PATH);
            boolean isAdmin = path.startsWith(ADMIN_PATH);
            // 自身查询不记录，避免日志控制台自己刷日志
            if (isAdmin) {
                return;
            }
            if (isAgent || isInternalMemory) {
                String level = status >= 500
                        ? AdminLogEvent.LEVEL_ERROR
                        : (status >= 400 ? AdminLogEvent.LEVEL_WARN : AdminLogEvent.LEVEL_INFO);
                String normalizedPath = normalizePath(path);
                // REQUEST 类别只用 httpMethod/path/httpStatus 表达响应信息；
                // statusFrom / statusTo 是业务状态机字段，专用于 BUSINESS_ACTION，
                // REQUEST 一律保持 null，避免一个事实保存两份。
                adminLogBuffer.record(
                        level,
                        AdminLogEvent.CATEGORY_REQUEST,
                        "REQUEST_COMPLETED",
                        traceId,
                        null,
                        null,
                        null,
                        null,
                        durationMs,
                        null,
                        sanitizeMethod(request.getMethod()),
                        normalizedPath,
                        status);
                return;
            }
        } catch (RuntimeException ex) {
            // 日志记录失败不能影响主请求
            log.warn("[{}] admin log 写入失败: {}", traceId, ex.getMessage());
        }
    }

    /**
     * 规范化路径：把动态段（id / conversationId）替换为占位符。
     * 不允许在日志中保留：
     *   - 原始 actionId（act_xxx）
     *   - 原始 conversationId（uuid / 任意字符串）
     *   - query string
     */
    static String normalizePath(String path) {
        if (path == null) return null;
        // 去掉 query string（如果有）
        int q = path.indexOf('?');
        String core = q >= 0 ? path.substring(0, q) : path;
        // /api/agent/actions/{actionId}/{decision}
        core = core.replaceAll("(/api/agent/actions/)[^/]+(/confirm|/cancel)",
                "$1{id}$2");
        // /api/internal/memory/conversations/{conversationId}/write
        core = core.replaceAll("(/api/internal/memory/conversations/)[^/]+(/write)",
                "$1{conversationId}$2");
        return core;
    }

    private static String sanitizeMethod(String method) {
        if (method == null) return null;
        // 仅允许 HTTP 标准方法
        return switch (method.toUpperCase()) {
            case "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD" -> method.toUpperCase();
            default -> null;
        };
    }
}