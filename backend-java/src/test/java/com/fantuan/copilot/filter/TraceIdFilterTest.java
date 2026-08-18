package com.fantuan.copilot.filter;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TraceIdFilterTest {

    private final TraceIdFilter filter = new TraceIdFilter();

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
}
