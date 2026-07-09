package com.fantuan.copilot.filter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
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
 */
@Component
public class TraceIdFilter extends OncePerRequestFilter {

    private static final String HEADER = "X-Trace-Id";
    private static final String MDC_KEY = "traceId";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {
        // 服务端统一生成 traceId，不读取客户端传入的 X-Trace-Id
        String traceId = UUID.randomUUID().toString();

        // 存入 MDC（日志自动带上）和 request attribute（Controller 可取用）
        MDC.put(MDC_KEY, traceId);
        request.setAttribute(MDC_KEY, traceId);

        // 设置响应头
        response.setHeader(HEADER, traceId);

        try {
            filterChain.doFilter(request, response);
        } finally {
            // 清理 MDC，防止线程复用污染
            MDC.remove(MDC_KEY);
        }
    }
}
