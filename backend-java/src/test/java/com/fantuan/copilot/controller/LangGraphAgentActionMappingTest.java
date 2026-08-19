package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import com.fantuan.copilot.service.demo.DemoRole;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestTemplate;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LangGraphAgentActionMappingTest {
    @Test
    void invalidIdentityIsRejectedBeforePythonWithoutLeakingPresentedValue() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        DemoIdentityService identities = mock(DemoIdentityService.class);
        when(identities.isEnabled()).thenReturn(true);
        when(identities.requireIdentity("unknown-sensitive-value")).thenThrow(new ActionException(
                HttpStatus.FORBIDDEN, "DEMO_IDENTITY_INVALID", "演示身份无效。", null, null));
        LangGraphAgentController controller = new LangGraphAgentController(restTemplate, bulkhead,
                mock(AdminAccessService.class), mock(BusinessActionService.class), identities);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("identity-trace");
        when(request.getHeader("X-Demo-User-Id")).thenReturn("unknown-sensitive-value");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);

        assertEquals(HttpStatus.FORBIDDEN, response.getStatusCode());
        assertNotNull(response.getBody());
        assertEquals("error", response.getBody().route());
        assertEquals("demo_identity", response.getBody().category());
        assertFalse(response.getBody().success());
        assertFalse(response.getBody().answer().contains("unknown-sensitive-value"));
        verifyNoInteractions(restTemplate);
        assertEquals(0, bulkhead.snapshot().get("active"));
    }

    @Test
    void permitIsReleasedBeforePendingActionCreationAndAdminTokenIsNotForwarded() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        DemoIdentityService identities = mock(DemoIdentityService.class);
        DemoIdentity identity = new DemoIdentity(
                "DEMO-001", "DEMO-001", "Demo User", DemoRole.EMPLOYEE);
        when(identities.isEnabled()).thenReturn(true);
        when(identities.requireIdentity("DEMO-001")).thenReturn(identity);
        when(admin.isAdmin("admin")).thenReturn(true);
        when(actionService.isAllowed("admin")).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20), "私事", HalfDay.NONE);
        PythonAgentResponse python = new PythonAgentResponse("draft", "action", true,
                "business_action", "", List.of(), true, "python-trace-999", proposal, List.of());
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));
        when(actionService.createPending(eq(proposal), eq("java-trace-123"), eq("admin"), eq(identity)))
                .thenAnswer(invocation -> {
                    assertEquals(0, bulkhead.snapshot().get("active"));
                    return mock(PendingActionView.class);
                });

        LangGraphAgentController controller = new LangGraphAgentController(
                restTemplate, bulkhead, admin, actionService, identities);
        ReflectionTestUtils.setField(controller, "agentBaseUrl", "http://python-agent");
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("java-trace-123");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");
        when(request.getHeader("X-Demo-User-Id")).thenReturn("DEMO-001");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);
        assertNotNull(response.getBody());
        assertNotNull(response.getBody().pendingAction());

        ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(anyString(), entity.capture(), eq(PythonAgentResponse.class));
        assertEquals("true", entity.getValue().getHeaders().getFirst("X-Allow-Business-Actions"));
        assertEquals("2026-07-16", entity.getValue().getHeaders().getFirst("X-Business-Date"));
        assertFalse(entity.getValue().getHeaders().containsKey("X-Admin-Token"));
        assertFalse(entity.getValue().getHeaders().containsKey("X-Demo-User-Id"));
        verify(actionService).createPending(proposal, "java-trace-123", "admin", identity);
        verify(actionService, never()).createPending(proposal, "python-trace-999", "admin", identity);
    }

    @Test
    void jwtIdentityWinsOverForgedEmployeeDemoAndQueryValues() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        DemoIdentityService identities = mock(DemoIdentityService.class);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class),
                eq(PythonAgentResponse.class))).thenReturn(ResponseEntity.ok(
                new PythonAgentResponse("ok", "rag", true, "normal", "", List.of(),
                        true, "python-trace", null, List.of())));

        AuthenticatedUser zhangsan = new AuthenticatedUser(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true);
        SecurityContextHolder.getContext().setAuthentication(
                UsernamePasswordAuthenticationToken.authenticated(
                        zhangsan, null,
                        List.of(new SimpleGrantedAuthority("ROLE_EMPLOYEE"))));
        try {
            LangGraphAgentController controller = new LangGraphAgentController(
                    restTemplate, bulkhead, mock(AdminAccessService.class), actionService, identities);
            ReflectionTestUtils.setField(controller, "agentBaseUrl", "http://python-agent");
            HttpServletRequest request = mock(HttpServletRequest.class);
            when(request.getAttribute("traceId")).thenReturn("spoof-trace");
            when(request.getHeader("X-Employee-Id")).thenReturn("E10002");
            when(request.getHeader("X-Demo-User-Id")).thenReturn("DEMO-002");
            when(request.getParameter("employee_id")).thenReturn("E10002");

            controller.langgraphChat(new ChatRequest("request"), request);

            ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
            verify(restTemplate).postForEntity(anyString(), entity.capture(),
                    eq(PythonAgentResponse.class));
            assertEquals("E10001", entity.getValue().getHeaders().getFirst("X-Employee-Id"));
            verifyNoInteractions(identities);
        } finally {
            SecurityContextHolder.clearContext();
        }
    }
}
