package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionHitlCoordinator;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.action.TaskRuntimeRegistrationRejectionException;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.task.TaskRuntimeService;
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
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LangGraphAgentActionMappingTest {
    @Test
    void publicDemoBusinessProposalIsRejectedBeforePendingActionPersistence() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        VerifiedIdentity publicDemo = new VerifiedIdentity(
                "U10000", "demo", "E10000", "公开演示账号",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(publicDemo);
        when(actionService.isAllowed(any(), eq(publicDemo))).thenReturn(false);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 7, 16));
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 7, 20),
                LocalDate.of(2026, 7, 20), "公开 demo 不应写入", HalfDay.NONE);
        PythonAgentResponse python = new PythonAgentResponse("业务动作", "action", true,
                "business_action", "", List.of(), true, "python-trace", proposal,
                List.of(), null);
        when(restTemplate.postForEntity(anyString(), any(HttpEntity.class), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(python));

        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                admin, actionService, identityContext, mock(AiTaskMemoryService.class),
                new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("public-demo-trace");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("帮我请一天年假", "public-demo-conversation"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertEquals("business_action", response.getBody().category());
        verify(actionService, never()).createPending(
                any(), anyString(), anyString(), any(), anyString());
        ArgumentCaptor<HttpEntity> requestEntity = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(anyString(), requestEntity.capture(),
                eq(PythonAgentResponse.class));
        assertEquals("false", requestEntity.getValue().getHeaders()
                .getFirst("X-Allow-Business-Actions"));
    }

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
    void expenseReasonClarificationPersistsWithoutMemoryLlmProposal() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
                VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        PythonAgentResponse python = new PythonAgentResponse(
                "请提供本次报销原因。", "action", true, "business_action", "", List.of(),
                true, "python-trace", null, List.of("reason"), null);
        when(gateway.post(anyString(), any(), any(), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(python);

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, mock(AdminAccessService.class), actionService, identityContext,
                memoryService, new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("expense-reason-trace");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("根据最近一次已批准出差准备报销", "expense-reason-conv"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        verify(memoryService).startNewActiveExpenseReasonCycle(
                "U10001", "expense-reason-conv", "根据最近一次已批准出差准备报销");
        verify(memoryService, never()).upsertActiveFromAgent(
                anyString(), anyString(), anyString(), anyMap(), anyString());
    }

    @Test
    void leaveReasonClarificationDoesNotCreateExpenseContinuation() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
                VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(gateway.post(anyString(), any(), any(), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(new PythonAgentResponse(
                        "请提供请假原因。", "action", true, "business_action", "", List.of(),
                        true, "python-trace", null, List.of("reason"), null));

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, mock(AdminAccessService.class), actionService, identityContext,
                memoryService, new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("leave-reason-trace");

        controller.langgraphChat(new ChatRequest("申请年假", "leave-reason-conv"), request);

        verify(memoryService, never()).upsertActiveExpenseReasonContinuation(
                anyString(), anyString(), anyString());
    }

    @Test
    void invalidIdentityIsRejectedBeforePythonWithoutLeakingPresentedValue() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenThrow(new ActionException(
                HttpStatus.UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "请先登录。", null, null));
        LangGraphAgentController controller = new LangGraphAgentController(
                new PythonAgentGateway(restTemplate, bulkhead, "http://python-agent"),
                mock(AdminAccessService.class), mock(BusinessActionService.class),
                identityContext, mock(AiTaskMemoryService.class),
                new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("identity-trace");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);

        assertEquals(HttpStatus.UNAUTHORIZED, response.getStatusCode());
        assertNotNull(response.getBody());
        assertEquals("error", response.getBody().route());
        assertEquals("authentication", response.getBody().category());
        assertFalse(response.getBody().success());
        assertEquals("请先登录。", response.getBody().answer());
        verifyNoInteractions(restTemplate);
        assertEquals(0, bulkhead.snapshot().get("active"));
    }

    @Test
    void permitIsReleasedBeforePendingActionCreationAndAdminTokenIsNotForwarded() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
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
                admin, actionService, identityContext,
                memoryService,
                new AdminLogBuffer());
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("java-trace-123");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request"), request);
        assertNotNull(response.getBody());
        assertNotNull(response.getBody().pendingAction());

        ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(anyString(), entity.capture(), eq(PythonAgentResponse.class));
        assertEquals("true", entity.getValue().getHeaders().getFirst("X-Allow-Business-Actions"));
        assertEquals("true", entity.getValue().getHeaders().getFirst("X-Allow-Eval"));
        assertEquals("2026-07-16", entity.getValue().getHeaders().getFirst("X-Business-Date"));
        assertFalse(entity.getValue().getHeaders().containsKey("X-Admin-Token"));
        assertFalse(entity.getValue().getHeaders().containsKey("X-Memory-Write-Scope"));
        var inOrder = inOrder(actionService, memoryService);
        inOrder.verify(actionService).createPending(eq(proposal), eq("java-trace-123"),
                eq("admin"), eq(identity), anyString());
        inOrder.verify(memoryService).upsertActiveFromAgent(eq("U10001"), anyString(),
                eq("LEAVE_REQUEST"), eq(Map.of("phase", "pending_confirmation")), eq("等待确认"));
        verify(actionService, never()).createPending(eq(proposal), eq("python-trace-999"),
                eq("admin"), eq(identity), anyString());
        verify(admin, never()).isAdmin("admin");
    }

    @Test
    void conversationInProgressKeepsExistingMemoryActive() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
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
                eq(identity), anyString()))
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
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        IdentityContext identityContext = mock(IdentityContext.class);
        when(identityContext.require(any())).thenReturn(identity);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
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
                eq(identity), anyString()))
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
    void jwtIdentityWinsOverForgedEmployeeAndQueryValues() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);
        BusinessActionService actionService = mock(BusinessActionService.class);
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
                    new IdentityContext(), mock(AiTaskMemoryService.class),
                    new AdminLogBuffer());
            HttpServletRequest request = mock(HttpServletRequest.class);
            when(request.getAttribute("traceId")).thenReturn("spoof-trace");
            when(request.getHeader("X-Employee-Id")).thenReturn("E10002");
            when(request.getParameter("employee_id")).thenReturn("E10002");

            controller.langgraphChat(new ChatRequest("request"), request);

            ArgumentCaptor<HttpEntity> entity = ArgumentCaptor.forClass(HttpEntity.class);
            verify(restTemplate).postForEntity(anyString(), entity.capture(),
                    eq(PythonAgentResponse.class));
            assertEquals("E10001", entity.getValue().getHeaders().getFirst("X-Employee-Id"));
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    @Test
    void taskRuntimeWaitingUserPersistsMemoryAfterAtomicActionLink() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        IdentityContext identityContext = mock(IdentityContext.class);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        AgentRuntimeThreadIdService threadIds = mock(AgentRuntimeThreadIdService.class);
        AgentRuntimeThreadExecutionGuard guard = mock(AgentRuntimeThreadExecutionGuard.class);
        BusinessActionHitlCoordinator hitl = mock(BusinessActionHitlCoordinator.class);
        TaskRuntimeService runtime = mock(TaskRuntimeService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution task = new TaskExecution("group-1", "task-1", identity.userId(),
                "conv-1", 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 9, 1),
                LocalDate.of(2026, 9, 1), "私事", HalfDay.NONE);
        HitlWaitMarker wait = new HitlWaitMarker(1, "BUSINESS_ACTION_CONFIRMATION",
                "wait_" + "b".repeat(64), "ex_" + "a".repeat(32),
                BusinessActionType.ANNUAL_LEAVE_REQUEST);
        PendingActionView pending = new PendingActionView("act-1",
                BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.PENDING_CONFIRMATION,
                "请假", null, "nonce", Instant.now(), true);
        AgentMemoryProposal memory = new AgentMemoryProposal(
                "LEAVE_REQUEST", Map.of("waiting_for", "confirmation"), "等待确认");

        when(identityContext.require(any())).thenReturn(identity);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(threadIds.generate(identity.userId(), "conv-1")).thenReturn("conversation-thread");
        when(threadIds.generate(identity.userId(), "conv-1", "task-1"))
                .thenReturn("task-thread");
        when(guard.tryAcquire("conversation-thread")).thenReturn(true);
        when(hitl.reconcileExpiredBeforeChat("trace", "admin", identity, "conv-1"))
                .thenReturn(true);
        when(runtime.reconcile(identity.userId(), "conv-1")).thenReturn(Optional.of(task));
        when(memoryService.find(identity.userId(), "conv-1")).thenReturn(Optional.empty());
        when(gateway.post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), eq("trace"))).thenReturn(
                new PythonAgentResponse("请确认", "action", true, "business_action", "",
                        List.of(), true, "trace", proposal, List.of(), memory, wait, null));
        when(runtime.matchesTaskType(task, TaskType.LEAVE_REQUEST)).thenReturn(true);
        when(hitl.registerWait(eq(proposal), eq(wait), eq("trace"), eq("admin"),
                eq(identity), eq("conv-1"), eq("task-1"))).thenReturn(pending);
        when(runtime.markWaitingUser("task-1", "act-1")).thenReturn(true);

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, admin, actionService, identityContext, memoryService,
                new AdminLogBuffer(), threadIds, guard, hitl, runtime);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("trace");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        var order = inOrder(runtime, memoryService);
        order.verify(runtime).markWaitingUser("task-1", "act-1");
        order.verify(memoryService).upsertActiveFromAgent(identity.userId(), "conv-1",
                "LEAVE_REQUEST", Map.of("waiting_for", "confirmation"), "等待确认");
    }

    @Test
    void taskRuntimeDeterministicRejectionReturnsSuccessorPendingAction() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        IdentityContext identityContext = mock(IdentityContext.class);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        AgentRuntimeThreadIdService threadIds = mock(AgentRuntimeThreadIdService.class);
        AgentRuntimeThreadExecutionGuard guard = mock(AgentRuntimeThreadExecutionGuard.class);
        BusinessActionHitlCoordinator hitl = mock(BusinessActionHitlCoordinator.class);
        TaskRuntimeService runtime = mock(TaskRuntimeService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution task = new TaskExecution("group-1", "task-1", identity.userId(),
                "conv-1", 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 9, 1),
                LocalDate.of(2026, 9, 1), "私事", HalfDay.NONE);
        HitlWaitMarker wait = new HitlWaitMarker(1, "BUSINESS_ACTION_CONFIRMATION",
                "wait_" + "b".repeat(64), "ex_" + "a".repeat(32),
                BusinessActionType.ANNUAL_LEAVE_REQUEST);
        PendingActionView successor = new PendingActionView("act-2",
                BusinessActionType.EXPENSE_CLAIM, ActionStatus.PENDING_CONFIRMATION,
                "报销", null, "nonce", Instant.now(), true);
        ActionException rejection = new ActionException(
                HttpStatus.UNPROCESSABLE_ENTITY, "BUSINESS_RULE_VIOLATION",
                "日期范围与已提交的模拟申请冲突。", null, null);

        when(identityContext.require(any())).thenReturn(identity);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
        when(actionService.hasBlockingAction(identity.userId(), "conv-1"))
                .thenReturn(false, true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(threadIds.generate(identity.userId(), "conv-1")).thenReturn("conversation-thread");
        when(threadIds.generate(identity.userId(), "conv-1", "task-1"))
                .thenReturn("task-thread");
        when(guard.tryAcquire("conversation-thread")).thenReturn(true);
        when(hitl.reconcileExpiredBeforeChat("trace", "admin", identity, "conv-1"))
                .thenReturn(true);
        when(runtime.reconcile(identity.userId(), "conv-1")).thenReturn(Optional.of(task));
        when(memoryService.find(identity.userId(), "conv-1")).thenReturn(Optional.empty());
        when(gateway.post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), eq("trace"))).thenReturn(
                new PythonAgentResponse("已生成请假申请", "action", true, "business_action", "",
                        List.of(), true, "trace", proposal, List.of(), null, wait, null));
        when(runtime.matchesTaskType(task, TaskType.LEAVE_REQUEST)).thenReturn(true);
        when(hitl.registerWait(eq(proposal), eq(wait), eq("trace"), eq("admin"),
                eq(identity), eq("conv-1"), eq("task-1")))
                .thenThrow(new TaskRuntimeRegistrationRejectionException(rejection, successor));

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, admin, actionService, identityContext, memoryService,
                new AdminLogBuffer(), threadIds, guard, hitl, runtime);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("trace");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertEquals("conv-1", response.getHeaders().getFirst("X-Conversation-Id"));
        assertEquals("no-store", response.getHeaders().getCacheControl());
        assertNotNull(response.getBody());
        assertTrue(response.getBody().success());
        assertEquals("action", response.getBody().route());
        assertEquals("business_action", response.getBody().category());
        assertEquals("上一项申请因业务规则未能生成，已继续处理下一项任务，请确认。",
                response.getBody().answer());
        assertSame(successor, response.getBody().pendingAction());
        verify(runtime, never()).markWaitingUser(anyString(), anyString());

        ResponseEntity<AgentChatResponse> duplicate = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, duplicate.getStatusCode());
        assertNotNull(duplicate.getBody());
        assertEquals("当前会话已有待确认的申请，请先确认或取消后再发起新申请。",
                duplicate.getBody().answer());
        verify(gateway, times(1)).post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), eq("trace"));
        verify(hitl, times(1)).registerWait(eq(proposal), eq(wait), eq("trace"),
                eq("admin"), eq(identity), eq("conv-1"), eq("task-1"));
    }

    @Test
    void taskRuntimeClarificationPersistsMemoryAfterWaitingState() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        IdentityContext identityContext = mock(IdentityContext.class);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        AgentRuntimeThreadIdService threadIds = mock(AgentRuntimeThreadIdService.class);
        AgentRuntimeThreadExecutionGuard guard = mock(AgentRuntimeThreadExecutionGuard.class);
        TaskRuntimeService runtime = mock(TaskRuntimeService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution task = new TaskExecution("group-1", "task-1", identity.userId(),
                "conv-1", 1, TaskType.EXPENSE_CLAIM, "报销", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AgentMemoryProposal memory = new AgentMemoryProposal(
                "EXPENSE_CLAIM", Map.of("waiting_for", "invoice"), "等待补充发票");

        when(identityContext.require(any())).thenReturn(identity);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(threadIds.generate(identity.userId(), "conv-1")).thenReturn("conversation-thread");
        when(threadIds.generate(identity.userId(), "conv-1", "task-1"))
                .thenReturn("task-thread");
        when(guard.tryAcquire("conversation-thread")).thenReturn(true);
        when(runtime.reconcile(identity.userId(), "conv-1")).thenReturn(Optional.of(task));
        when(memoryService.find(identity.userId(), "conv-1")).thenReturn(Optional.empty());
        when(gateway.post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), eq("trace"))).thenReturn(
                new PythonAgentResponse("请补充发票", "action", true, "business_action", "",
                        List.of(), true, "trace", null, List.of("invoice"), memory));
        when(runtime.markWaitingClarification("task-1")).thenReturn(true);

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, admin, actionService, identityContext, memoryService,
                new AdminLogBuffer(), threadIds, guard, null, runtime);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("trace");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        verify(runtime).markWaitingClarification("task-1");
        verify(memoryService).upsertActiveFromAgent(identity.userId(), "conv-1",
                "EXPENSE_CLAIM", Map.of("waiting_for", "invoice"), "等待补充发票");
    }

    @Test
    void taskRuntimeFailureAbandonsMemoryBeforeSuccessorContinuation() {
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        BusinessActionService actionService = mock(BusinessActionService.class);
        AdminAccessService admin = mock(AdminAccessService.class);
        IdentityContext identityContext = mock(IdentityContext.class);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        AgentRuntimeThreadIdService threadIds = mock(AgentRuntimeThreadIdService.class);
        AgentRuntimeThreadExecutionGuard guard = mock(AgentRuntimeThreadExecutionGuard.class);
        BusinessActionHitlCoordinator hitl = mock(BusinessActionHitlCoordinator.class);
        TaskRuntimeService runtime = mock(TaskRuntimeService.class);
        VerifiedIdentity identity = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution task = new TaskExecution("group-1", "task-1", identity.userId(),
                "conv-1", 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AgentMemoryProposal staleMemory = new AgentMemoryProposal(
                "LEAVE_REQUEST", Map.of("step", "failed"), "旧任务结果");

        when(identityContext.require(any())).thenReturn(identity);
        when(admin.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.isAllowed(eq("admin"), any(VerifiedIdentity.class))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(threadIds.generate(identity.userId(), "conv-1")).thenReturn("conversation-thread");
        when(threadIds.generate(identity.userId(), "conv-1", "task-1"))
                .thenReturn("task-thread");
        when(guard.tryAcquire("conversation-thread")).thenReturn(true);
        when(hitl.reconcileExpiredBeforeChat("trace", "admin", identity, "conv-1"))
                .thenReturn(true);
        when(runtime.reconcile(identity.userId(), "conv-1")).thenReturn(Optional.of(task));
        when(memoryService.find(identity.userId(), "conv-1")).thenReturn(Optional.empty());
        when(gateway.post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), eq("trace"))).thenReturn(
                new PythonAgentResponse("无法继续", "action", true, "business_action", "",
                        List.of(), true, "trace", null, List.of(), staleMemory));
        when(runtime.markTerminal("task-1", TaskExecutionStatus.FAILED)).thenReturn(true);
        when(hitl.startNextTaskAfterTerminal(task, identity, "admin", "trace"))
                .thenReturn(null);

        LangGraphAgentController controller = new LangGraphAgentController(
                gateway, admin, actionService, identityContext, memoryService,
                new AdminLogBuffer(), threadIds, guard, hitl, runtime);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn("trace");
        when(request.getHeader("X-Admin-Token")).thenReturn("admin");

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest("request", "conv-1"), request);

        assertEquals(HttpStatus.OK, response.getStatusCode());
        verify(memoryService).abandon(identity.userId(), "conv-1");
        verify(memoryService, never()).upsertActiveFromAgent(
                anyString(), anyString(), any(), any(), anyString());
        var order = inOrder(runtime, memoryService, hitl);
        order.verify(runtime).markTerminal("task-1", TaskExecutionStatus.FAILED);
        order.verify(memoryService).abandon(identity.userId(), "conv-1");
        order.verify(hitl).startNextTaskAfterTerminal(task, identity, "admin", "trace");
    }

}
