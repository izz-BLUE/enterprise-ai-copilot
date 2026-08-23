package com.fantuan.copilot.filter;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TraceIdFilterTest {

    private final TraceIdFilter filter = new TraceIdFilter(new AdminLogBuffer());

    @Test
    void internalRequestWithTraceIdPassesThroughOriginalTraceId() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/internal/leave/balance");
        request.addHeader("X-Trace-Id", "upstream-trace-id");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = (servletRequest, servletResponse) -> {
            assertEquals("upstream-trace-id", servletRequest.getAttribute("traceId"));
        };

        filter.doFilter(request, response, chain);

        assertEquals("upstream-trace-id", response.getHeader("X-Trace-Id"));
    }

    @Test
    void externalRequestWithTraceIdGeneratesNewTraceId() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/agent/chat");
        request.addHeader("X-Trace-Id", "client-supplied-trace-id");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = (servletRequest, servletResponse) -> {
            String traceId = (String) servletRequest.getAttribute("traceId");
            assertNotEquals("client-supplied-trace-id", traceId);
            assertTrue(traceId.matches("[0-9a-f-]{36}"));
        };

        filter.doFilter(request, response, chain);

        assertNotEquals("client-supplied-trace-id", response.getHeader("X-Trace-Id"));
        assertTrue(response.getHeader("X-Trace-Id").matches("[0-9a-f-]{36}"));
    }

    /**
     * confirmationNonce 哨兵真实进入 confirm 请求 body 后：
     * REQUEST 日志由 TraceIdFilter 产生，它只读 method / URI / status，
     * 不读取、不记录请求 body，因此日志快照必须完全不含 nonce 与
     * Authorization 哨兵；path 必须规范化为 {id} 占位符。
     *
     * 说明：BusinessActionService 的审计入口本身从不接收原始 nonce——
     * 原始 nonce 只存在于 confirm 请求 body，日志侧唯一接触该请求的就是
     * TraceIdFilter；它不读取 body，故 nonce 永不进入日志缓冲区。
     */
    @Test
    void adminConfirmRequestLogNeverLeaksNonceOrAuthorizationSentinel() throws Exception {
        AdminLogBuffer buffer = new AdminLogBuffer();
        TraceIdFilter filter = new TraceIdFilter(buffer);

        MockHttpServletRequest request = new MockHttpServletRequest(
                "POST", "/api/agent/actions/act-sensitive/confirm");
        request.setContent(("{\"confirmationNonce\":\"SENSITIVE_CONFIRMATION_NONCE_789\"}")
                .getBytes(StandardCharsets.UTF_8));
        request.addHeader("Authorization", "Bearer SENSITIVE_AUTH_HEADER_456");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = (servletRequest, servletResponse) ->
                ((HttpServletResponse) servletResponse).setStatus(200);

        filter.doFilter(request, response, chain);

        List<AdminLogEvent> events = buffer.snapshot(
                null, AdminLogEvent.CATEGORY_REQUEST, null, 50);
        assertEquals(1, events.size());
        AdminLogEvent event = events.get(0);
        assertEquals("POST", event.httpMethod());
        assertEquals("/api/agent/actions/{id}/confirm", event.path());
        assertEquals(200, event.httpStatus());
        // REQUEST 事件不带业务状态机字段
        assertNull(event.statusFrom());
        assertNull(event.statusTo());

        String json = new ObjectMapper().registerModule(new JavaTimeModule())
                .writeValueAsString(events);
        assertFalse(json.contains("SENSITIVE_CONFIRMATION_NONCE_789"));
        assertFalse(json.contains("SENSITIVE_AUTH_HEADER_456"));
        assertFalse(json.contains("act-sensitive"));
    }
}
