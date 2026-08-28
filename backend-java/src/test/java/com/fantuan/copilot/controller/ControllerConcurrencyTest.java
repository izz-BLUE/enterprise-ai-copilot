package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.client.RestTemplate;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ControllerConcurrencyTest {

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void standardControllerReturnsExplicit429WhenBulkheadIsFull() {
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        PythonAgentBulkhead.Permit held = bulkhead.tryAcquire("held");
        assertNotNull(held);

        HttpServletRequest servletRequest = requestWithTraceId("trace-standard");
        ChatController controller = new ChatController(new PythonAgentGateway(
                mock(RestTemplate.class), bulkhead, "http://python-agent"));
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
        BusinessActionService actionService = mock(BusinessActionService.class);
        when(actionService.businessDate()).thenReturn(java.time.LocalDate.of(2026, 8, 24));
        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(mock(RestTemplate.class), bulkhead, "http://python-agent"),
                mock(AdminAccessService.class),
                actionService, new IdentityContext(),
                mock(AiTaskMemoryService.class),
                new AdminLogBuffer());
        installJwt();
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

    private void installJwt() {
        AuthenticatedUser user = new AuthenticatedUser(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true);
        SecurityContextHolder.getContext().setAuthentication(
                UsernamePasswordAuthenticationToken.authenticated(
                        user, null, List.of(new SimpleGrantedAuthority("ROLE_EMPLOYEE"))));
    }
}
