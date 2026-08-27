package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.InternalAgentChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.gateway.python.PythonAgentBusyException;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class LangGraphAgentControllerThreadGuardTest {
    private static final String USER_ID = "U10001";
    private static final String CONVERSATION_ID = "conv-guard";
    private static final String ADMIN_TOKEN = "admin";

    private final AgentRuntimeThreadIdService threadIdService = new AgentRuntimeThreadIdService();
    private PythonAgentGateway pythonAgentGateway;
    private AdminAccessService adminAccessService;
    private BusinessActionService businessActionService;
    private IdentityContext identityContext;
    private AiTaskMemoryService memoryService;
    private AgentRuntimeThreadExecutionGuard guard;
    private LangGraphAgentController controller;

    @BeforeEach
    void setUp() {
        pythonAgentGateway = mock(PythonAgentGateway.class);
        adminAccessService = mock(AdminAccessService.class);
        businessActionService = mock(BusinessActionService.class);
        identityContext = mock(IdentityContext.class);
        memoryService = mock(AiTaskMemoryService.class);
        guard = new AgentRuntimeThreadExecutionGuard();

        when(identityContext.require(any())).thenReturn(new VerifiedIdentity(
                USER_ID, "user", "E10001", "Test User",
                com.fantuan.copilot.auth.AuthRole.EMPLOYEE, true,
                VerifiedIdentity.Source.JWT));
        when(adminAccessService.isAdmin(ADMIN_TOKEN)).thenReturn(false);
        when(businessActionService.isAllowed(ADMIN_TOKEN)).thenReturn(false);
        when(businessActionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));

        controller = new LangGraphAgentController(
                pythonAgentGateway, adminAccessService, businessActionService,
                identityContext, memoryService, new AdminLogBuffer(),
                threadIdService, guard);
    }

    @Test
    void busyBeforeMemoryReadReturns429AndDoesNotStartRequest() {
        String threadId = threadId();
        assertTrue(guard.tryAcquire(threadId));
        try {
            var response = controller.langgraphChat(new ChatRequest("继续", CONVERSATION_ID),
                    request("guard-busy"));

            assertEquals(HttpStatus.TOO_MANY_REQUESTS, response.getStatusCode());
            assertEquals("1", response.getHeaders().getFirst(HttpHeaders.RETRY_AFTER));
            verifyNoInteractions(memoryService, pythonAgentGateway);
            verify(businessActionService, never()).createPending(
                    any(), anyString(), anyString(), any(), anyString());
        } finally {
            guard.release(threadId);
        }
    }

    @Test
    void pythonFailureReleasesGuardAndNextRequestCanRun() {
        PythonAgentResponse success = response(null);
        AtomicInteger calls = new AtomicInteger();
        doAnswer(invocation -> {
            if (calls.getAndIncrement() == 0) {
                throw new PythonAgentTransportException(
                        HttpStatus.BAD_GATEWAY, "transport failed", null);
            }
            return success;
        }).when(pythonAgentGateway).post(
                eq("/agent/langgraph/chat"), any(InternalAgentChatRequest.class),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());

        var first = controller.langgraphChat(new ChatRequest("继续", CONVERSATION_ID),
                request("guard-python-failure-1"));
        assertEquals(HttpStatus.BAD_GATEWAY, first.getStatusCode());

        String threadId = threadId();
        assertTrue(guard.tryAcquire(threadId));
        guard.release(threadId);

        var second = controller.langgraphChat(new ChatRequest("继续", CONVERSATION_ID),
                request("guard-python-failure-2"));
        assertEquals(HttpStatus.OK, second.getStatusCode());
        assertEquals(2, calls.get());
    }

    @Test
    void pythonBusyFailureReleasesGuard() {
        AtomicInteger calls = new AtomicInteger();
        doAnswer(invocation -> {
            if (calls.getAndIncrement() == 0) {
                throw new PythonAgentBusyException();
            }
            return response(null);
        }).when(pythonAgentGateway).post(
                eq("/agent/langgraph/chat"), any(InternalAgentChatRequest.class),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());

        var first = controller.langgraphChat(new ChatRequest("继续", CONVERSATION_ID),
                request("guard-python-busy-1"));
        assertEquals(HttpStatus.TOO_MANY_REQUESTS, first.getStatusCode());

        String threadId = threadId();
        assertTrue(guard.tryAcquire(threadId));
        guard.release(threadId);

        var second = controller.langgraphChat(new ChatRequest("继续", CONVERSATION_ID),
                request("guard-python-busy-2"));
        assertEquals(HttpStatus.OK, second.getStatusCode());
    }

    @Test
    void recoveryConflictReturns409WithoutMemoryOrPendingAction() {
        doAnswer(invocation -> {
            throw new PythonAgentTransportException(
                    HttpStatus.CONFLICT, "recovery conflict", null);
        }).when(pythonAgentGateway).post(
                eq("/agent/langgraph/chat"), any(InternalAgentChatRequest.class),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());

        var response = controller.langgraphChat(
                new ChatRequest("继续报销", CONVERSATION_ID), request("guard-recovery-conflict"));

        assertEquals(HttpStatus.CONFLICT, response.getStatusCode());
        assertEquals("recovery_conflict", response.getBody().category());
        assertFalse(response.getBody().success());
        assertTrue(response.getBody().answer().contains("未完成的 Agent 执行"));
        verify(memoryService, never()).upsertActiveFromAgent(
                anyString(), anyString(), anyString(), anyMap(), anyString());
        verify(businessActionService, never()).createPending(
                any(), anyString(), anyString(), any(), anyString());
        assertTrue(guard.tryAcquire(threadId()));
        guard.release(threadId());
    }

    @Test
    void pendingActionFailureReleasesGuard() {
        when(businessActionService.isAllowed(ADMIN_TOKEN)).thenReturn(true);
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST,
                LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1),
                "私事", HalfDay.NONE);
        doReturn(response(proposal)).when(pythonAgentGateway).post(
                eq("/agent/langgraph/chat"), any(InternalAgentChatRequest.class),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());
        when(businessActionService.createPending(
                any(), anyString(), anyString(), any(), anyString()))
                .thenThrow(new ActionException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "BUSINESS_RULE_VIOLATION",
                        "invalid proposal", null, null));

        var response = controller.langgraphChat(
                new ChatRequest("申请年假", CONVERSATION_ID), request("guard-pending-failure"));

        assertEquals(HttpStatus.OK, response.getStatusCode());
        verify(businessActionService).createPending(
                any(), anyString(), anyString(), any(), anyString());
        assertTrue(guard.tryAcquire(threadId()));
        guard.release(threadId());
    }

    @Test
    void normalLifecycleKeepsThreadActiveFromMemoryReadThroughMemoryPersist() {
        String threadId = threadId();
        AtomicBoolean memoryReadInsideGuard = new AtomicBoolean();
        AtomicBoolean pythonInsideGuard = new AtomicBoolean();
        AtomicBoolean memoryPersistInsideGuard = new AtomicBoolean();
        when(memoryService.find(USER_ID, CONVERSATION_ID)).thenAnswer(invocation -> {
            memoryReadInsideGuard.set(!guard.tryAcquire(threadId));
            return Optional.empty();
        });
        doAnswer(invocation -> {
            pythonInsideGuard.set(!guard.tryAcquire(threadId));
            return response(new AgentMemoryProposal(
                    "EXPENSE_REQUEST", Map.of("phase", "progress"), "进行中"));
        }).when(pythonAgentGateway).post(
                eq("/agent/langgraph/chat"), any(InternalAgentChatRequest.class),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());
        doAnswer(invocation -> {
            memoryPersistInsideGuard.set(!guard.tryAcquire(threadId));
            return null;
        }).when(memoryService).upsertActiveFromAgent(
                anyString(), anyString(), anyString(), anyMap(), anyString());

        var response = controller.langgraphChat(
                new ChatRequest("继续报销", CONVERSATION_ID), request("guard-normal"));

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertTrue(memoryReadInsideGuard.get());
        assertTrue(pythonInsideGuard.get());
        assertTrue(memoryPersistInsideGuard.get());
        verify(memoryService).find(USER_ID, CONVERSATION_ID);
        verify(memoryService).upsertActiveFromAgent(
                eq(USER_ID), eq(CONVERSATION_ID), eq("EXPENSE_REQUEST"),
                eq(Map.of("phase", "progress")), eq("进行中"));
        assertTrue(guard.tryAcquire(threadId));
        guard.release(threadId);
    }

    private String threadId() {
        return threadIdService.generate(USER_ID, CONVERSATION_ID);
    }

    private HttpServletRequest request(String traceId) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn(traceId);
        when(request.getHeader("X-Admin-Token")).thenReturn(ADMIN_TOKEN);
        return request;
    }

    private PythonAgentResponse response(Object proposalOrMemory) {
        if (proposalOrMemory instanceof AnnualLeaveActionProposal proposal) {
            return new PythonAgentResponse("draft", "action", true, "business_action", "",
                    List.of(), true, "python-trace", proposal, List.of(), null);
        }
        return new PythonAgentResponse("answer", "rag", true, "normal", "", List.of(),
                true, "python-trace", null, List.of(),
                (AgentMemoryProposal) proposalOrMemory);
    }
}
