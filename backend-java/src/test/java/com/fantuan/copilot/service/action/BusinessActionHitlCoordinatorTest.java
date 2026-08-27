package com.fantuan.copilot.service.action;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.AnnualLeaveSummary;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertSame;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BusinessActionHitlCoordinatorTest {
    private static final String ADMIN_TOKEN = "admin-token";
    private static final String ACTION_ID = "act-hitl-001";
    private static final String CONVERSATION_ID = "conv-hitl";
    private static final String RUNTIME_THREAD_ID = "rt-hitl-thread";
    private static final VerifiedIdentity IDENTITY = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);

    @Mock BusinessActionService actionService;
    @Mock PendingActionRepository actions;
    @Mock PythonAgentGateway pythonAgentGateway;
    @Mock AgentRuntimeThreadIdService threadIdService;
    @Mock AgentRuntimeThreadExecutionGuard threadGuard;
    @Mock AdminAccessService adminAccessService;

    private BusinessActionHitlCoordinator coordinator;

    @BeforeEach
    void setUp() {
        coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService,
                threadGuard, adminAccessService);
    }

    private void stubResumeDependencies() {
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(true);
        when(adminAccessService.isAdmin(ADMIN_TOKEN)).thenReturn(true);
        when(actionService.isAllowed(ADMIN_TOKEN)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
    }

    @Test
    void confirmCommitsJavaActionBeforeBestEffortPythonResume() {
        stubResumeDependencies();
        PendingAction pending = pendingAction();
        ActionExecutionResponse committed = new ActionExecutionResponse(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.SUCCEEDED,
                "LR-202608-0001", "模拟年假申请已提交。", false,
                Instant.parse("2026-08-27T08:00:00Z"), "origin", "confirm-trace");
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(actionService.confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq("confirm-trace"), any())).thenReturn(committed);
        doReturn(new PythonAgentResponse(
                "confirmed", "action", true, "business_action", "", List.of(), true,
                "resume-trace", null, List.of(), null, null))
                .when(pythonAgentGateway).post(
                        eq("/agent/langgraph/hitl/resume"), any(), any(HttpHeaders.class),
                        eq(PythonAgentResponse.class), eq("confirm-trace"));

        ActionExecutionResponse actual = coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "confirm-trace", IDENTITY);

        assertSame(committed, actual);
        InOrder order = inOrder(actionService, pythonAgentGateway);
        order.verify(actionService).authorizeForAction(eq(ADMIN_TOKEN), any());
        order.verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq("confirm-trace"), any());
        order.verify(actionService).isAllowed(ADMIN_TOKEN);
        order.verify(actionService).businessDate();
        order.verify(pythonAgentGateway).post(
                eq("/agent/langgraph/hitl/resume"), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), eq("confirm-trace"));
        verify(threadGuard).release(RUNTIME_THREAD_ID);
    }

    @Test
    void pythonResumeFailureDoesNotUndoCommittedJavaResultOrLeakGuard() {
        stubResumeDependencies();
        PendingAction pending = pendingAction();
        ActionExecutionResponse committed = new ActionExecutionResponse(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.SUCCEEDED,
                "LR-202608-0001", "已提交。", false, Instant.now(), "origin", "trace");
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(actionService.confirm(anyString(), anyString(), anyString(), anyString(),
                anyString(), any())).thenReturn(committed);
        doThrow(new com.fantuan.copilot.gateway.python.PythonAgentTransportException(
                HttpStatus.BAD_GATEWAY, "python unavailable", null))
                .when(pythonAgentGateway).post(
                        eq("/agent/langgraph/hitl/resume"), any(), any(HttpHeaders.class),
                        eq(PythonAgentResponse.class), anyString());

        ActionExecutionResponse actual = coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "trace", IDENTITY);

        assertSame(committed, actual);
        verify(threadGuard).release(RUNTIME_THREAD_ID);
    }

    @Test
    void registrationDelegatesToJavaAuthorityWithoutCallingPython() {
        BusinessActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST,
                LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1), "私事", HalfDay.NONE);
        HitlWaitMarker wait = new HitlWaitMarker(
                1, "BUSINESS_ACTION_CONFIRMATION", "wait_" + "a".repeat(64),
                "ex_" + "b".repeat(32), BusinessActionType.ANNUAL_LEAVE_REQUEST);
        PendingActionView view = new PendingActionView(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST,
                ActionStatus.PENDING_CONFIRMATION, "年假申请", new AnnualLeaveSummary(
                        "E10001", LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1),
                        HalfDay.NONE, new BigDecimal("1.0"), "私事",
                        new BigDecimal("5.0"), new BigDecimal("4.0")),
                "nonce", Instant.parse("2026-08-28T00:00:00Z"), true);
        when(actionService.createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY.asDemoIdentity(),
                CONVERSATION_ID, wait.executionId(), wait.waitId())).thenReturn(view);
        when(actions.findByHitlWaitId(wait.waitId())).thenReturn(Optional.empty());

        PendingActionView actual = coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID);

        assertSame(view, actual);
        verify(actionService).createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY.asDemoIdentity(),
                CONVERSATION_ID, wait.executionId(), wait.waitId());
    }

    private static PendingAction pendingAction() {
        return PendingAction.pending(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST, "origin",
                IDENTITY.userId(), CONVERSATION_ID, IDENTITY.employeeId(), IDENTITY.displayName(),
                LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1), HalfDay.NONE,
                "私事", new BigDecimal("1.0"), new BigDecimal("5.0"),
                new BigDecimal("4.0"), new byte[32],
                Instant.parse("2026-08-27T07:00:00Z"),
                Instant.parse("2026-08-28T07:00:00Z"), "{}",
                "ex_" + "b".repeat(32), "wait_" + "a".repeat(64));
    }
}
