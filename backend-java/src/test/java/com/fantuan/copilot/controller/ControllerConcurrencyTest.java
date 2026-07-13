package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ControllerConcurrencyTest {

    @Test
    void standardControllerReturnsExplicit429WhenBulkheadIsFull() {
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        PythonAgentBulkhead.Permit held = bulkhead.tryAcquire("held");
        assertNotNull(held);

        HttpServletRequest servletRequest = requestWithTraceId("trace-standard");
        ChatController controller = new ChatController(mock(RestTemplate.class), bulkhead);
        ResponseEntity<ChatResponse> response = controller.chat(
                new ChatRequest("几点上班？"), servletRequest);

        assertEquals(HttpStatus.TOO_MANY_REQUESTS, response.getStatusCode());
        assertEquals("1", response.getHeaders().getFirst(HttpHeaders.RETRY_AFTER));
        assertNotNull(response.getBody());
        assertFalse(response.getBody().success());
        assertEquals("trace-standard", response.getBody().traceId());
        held.close();
    }

    @Test
    void agentControllerReturnsExplicit429WhenBulkheadIsFull() {
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        PythonAgentBulkhead.Permit held = bulkhead.tryAcquire("held");
        assertNotNull(held);

        HttpServletRequest servletRequest = requestWithTraceId("trace-agent");
        LangGraphAgentController controller = new LangGraphAgentController(
                mock(RestTemplate.class), bulkhead);
        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("几点上班？"), servletRequest);

        assertEquals(HttpStatus.TOO_MANY_REQUESTS, response.getStatusCode());
        assertEquals("1", response.getHeaders().getFirst(HttpHeaders.RETRY_AFTER));
        assertNotNull(response.getBody());
        assertEquals("busy", response.getBody().route());
        assertEquals("overloaded", response.getBody().category());
        assertEquals("trace-agent", response.getBody().traceId());
        held.close();
    }

    private HttpServletRequest requestWithTraceId(String traceId) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn(traceId);
        return request;
    }
}
