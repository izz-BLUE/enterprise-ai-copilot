package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import com.fantuan.copilot.service.demo.DemoRole;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.client.RestTemplate;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LangGraphAgentActionMappingTest {
    @Test
    void memoryPersistenceFailureDoesNotFailAgentResponse() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 24));
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        PythonAgentResponse python = new PythonAgentResponse(
                "请补充日期。", "action", true, "business_action", "", List.of(),
                true, "python-trace", null, List.of(),
                new AgentMemoryProposal("LEAVE_REQUEST", Map.of("waiting_for", "date"),
                        "等待补充日期"));
        when(gateway.post(anyString(), any(), any(), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(python);
        doThrow(new IllegalStateException("database unavailable"))
                .when(memoryService).upsertActiveFromAgent(
                        anyString(), anyString(), any(), any(), any());

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, mock(AdminAccessService.class), actionService, identityContext,
                memoryService, new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("java-trace");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertTrue(response.getBody().success());
        assertEquals("请补充日期。", response.getBody().answer());
        verify(memoryService).upsertActiveFromAgent(
                "U10001", "conv-1", "LEAVE_REQUEST",
                Map.of("waiting_for", "date"), "等待补充日期");
    }

    @Test
    void invalidIdentityIsRejectedBeforePythonWithoutLeakingPresentedValue() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        DemoIdentityService identities = mock(DemoIdentityService.class);
        when(identities.isEnabled()).thenReturn(true);
        when(identities.requireIdentity("unknown-sensitive-value")).thenThrow(new ActionException(
                HttpStatus.FORBIDDEN, "DEMO_IDENTITY_INVALID", "演示身份无效。", null, null));
        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                mock(AdminAccessService.class), mock(BusinessActionService.class),
                new IdentityContext(identities), mock(AiTaskMemoryService.class),
                new AdminLogBuffer());
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
        AgentMemoryProposal memoryProposal = new AgentMemoryProposal(
                "LEAVE_REQUEST", Map.of("phase", "pending_confirmation"), "等待确认");
        PythonAgentResponse python = new PythonAgentResponse("draft", "action", true,
                "business_action", "", List.of(), true, "python-trace-999", proposal,
                List.of(), memoryProposal);
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));
        when(actionService.createPending(eq(proposal), eq("java-trace-123"), eq("admin"),
                eq(identity), anyString()))
                .thenAnswer(invocation -> {
                    assertEquals(0, bulkhead.snapshot().get("active"));
                    return mock(PendingActionView.class);
                });

        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                admin, actionService, new IdentityContext(identities),
                memoryService,
                new AdminLogBuffer());
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
        assertFalse(entity.getValue().getHeaders().containsKey("X-Memory-Write-Scope"));
        var inOrder = inOrder(actionService, memoryService);
        inOrder.verify(actionService).createPending(eq(proposal), eq("java-trace-123"),
                eq("admin"), eq(identity), anyString());
        inOrder.verify(memoryService).upsertActiveFromAgent(eq("DEMO-001"), anyString(),
                eq("LEAVE_REQUEST"), eq(Map.of("phase", "pending_confirmation")), eq("等待确认"));
        verify(actionService, never()).createPending(eq(proposal), eq("python-trace-999"),
                eq("admin"), eq(identity), anyString());
    }

    @Test
    void conversationInProgressKeepsExistingMemoryActive() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "DEMO-001", "DEMO-001", "DEMO-001", "Demo User",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.DEMO);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(admin.isAdmin("admin")).thenReturn(true);
        when(actionService.isAllowed("admin")).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20), "私事", HalfDay.NONE);
        PythonAgentResponse python = new PythonAgentResponse("draft", "action", true,
                "business_action", "", List.of(), true, "python-trace-999", proposal,
                List.of(), new AgentMemoryProposal("LEAVE_REQUEST", Map.of("x", 1), "draft"));
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));
        when(actionService.createPending(eq(proposal), anyString(), eq("admin"),
                eq(identity.asDemoIdentity()), anyString()))
                .thenThrow(new ActionException(HttpStatus.CONFLICT,
                        "ACTION_CONVERSATION_IN_PROGRESS", "当前会话已有待确认的申请。", null, null));

        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                admin, actionService, identityContext, memoryService,
                new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("java-trace-123");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);
        assertNotNull(response.getBody());
        assertFalse(response.getBody().success());
        assertNull(response.getBody().pendingAction());
        assertEquals("当前会话已有待确认的申请，请先确认或取消后再发起新申请。",
                response.getBody().answer());
        verify(memoryService, never()).upsertActiveFromAgent(
                anyString(), anyString(), any(), any(), any());
        verify(memoryService, never()).abandon(anyString(), anyString());
    }

    @Test
    void otherActionFailureDoesNotWriteMemory() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "DEMO-001", "DEMO-001", "DEMO-001", "Demo User",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.DEMO);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(admin.isAdmin("admin")).thenReturn(true);
        when(actionService.isAllowed("admin")).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20), "私事", HalfDay.NONE);
        PythonAgentResponse python = new PythonAgentResponse("draft", "action", true,
                "business_action", "", List.of(), true, "python-trace-999", proposal,
                List.of(), new AgentMemoryProposal("LEAVE_REQUEST", Map.of("x", 1), "draft"));
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));
        when(actionService.createPending(eq(proposal), anyString(), eq("admin"),
                eq(identity.asDemoIdentity()), anyString()))
                .thenThrow(new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                        "BUSINESS_RULE_VIOLATION", "年假申请参数不完整。", null, null));

        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                admin, actionService, identityContext, memoryService,
                new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("java-trace-123");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);
        assertNotNull(response.getBody());
        assertFalse(response.getBody().success());
        assertEquals("暂时无法生成申请草稿，请检查信息后重试。", response.getBody().answer());
        verify(memoryService, never()).upsertActiveFromAgent(
                anyString(), anyString(), any(), any(), any());
        verify(memoryService, never()).abandon(anyString(), anyString());
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
                        true, "python-trace", null, List.of(), null)));

        AuthenticatedUser zhangsan = new AuthenticatedUser(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true);
        SecurityContextHolder.getContext().setAuthentication(
                UsernamePasswordAuthenticationToken.authenticated(
                        zhangsan, null,
                        List.of(new SimpleGrantedAuthority("ROLE_EMPLOYEE"))));
        try {
            LangGraphAgentController controller = new LangGraphAgentController(
                    new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                    mock(AdminAccessService.class), actionService,
                    new IdentityContext(identities), mock(AiTaskMemoryService.class),
                    new AdminLogBuffer());
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
