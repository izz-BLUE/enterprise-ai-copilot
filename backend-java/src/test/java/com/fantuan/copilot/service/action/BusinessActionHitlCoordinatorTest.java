package com.fantuan.copilot.service.action;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.AnnualLeaveSummary;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlResumePayload;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
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
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.Arguments;
import org.mockito.InOrder;
import org.mockito.ArgumentCaptor;
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
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
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
    @Mock ExpenseExternalApprovalCoordinator externalApprovalCoordinator;

    private BusinessActionHitlCoordinator coordinator;

    @BeforeEach
    void setUp() {
        coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService,
                threadGuard, adminAccessService, externalApprovalCoordinator);
    }

    private void stubResumeDependencies() {
        stubResumeDependencies(true);
    }

    private void stubResumeDependencies(boolean allowBusinessActions) {
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(true);
        when(adminAccessService.isAdminIdentity(IDENTITY)).thenReturn(true);
        when(actionService.isAllowed(ADMIN_TOKEN, IDENTITY)).thenReturn(allowBusinessActions);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
    }

    @Test
    void unexpiredOrMissingChatActionDoesNotResumePythonCheckpoint() {
        when(actionService.reconcileExpiredForChat(
                IDENTITY.userId(), CONVERSATION_ID, "chat-trace"))
                .thenReturn(Optional.empty());

        assertTrue(coordinator.reconcileExpiredBeforeChat(
                "chat-trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));

        verify(actionService).reconcileExpiredForChat(
                IDENTITY.userId(), CONVERSATION_ID, "chat-trace");
        verifyNoInteractions(pythonAgentGateway);
    }

    @Test
    void expiredChatActionUsesExistingExpiredResumeWithoutTakingGuardAgain() {
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(adminAccessService.isAdminIdentity(IDENTITY)).thenReturn(true);
        when(actionService.isAllowed(ADMIN_TOKEN, IDENTITY)).thenReturn(false);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        PendingAction expired = terminalAction(ActionStatus.EXPIRED);
        when(actionService.reconcileExpiredForChat(
                IDENTITY.userId(), CONVERSATION_ID, "chat-expired"))
                .thenReturn(Optional.of(expired));
        doReturn(new PythonAgentResponse("expired", "action", true, "business_action", "",
                List.of(), true, "resume-trace", null, List.of(), null))
                .when(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), any(),
                        any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("chat-expired"));

        assertTrue(coordinator.reconcileExpiredBeforeChat(
                "chat-expired", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));

        ArgumentCaptor<HitlResumePayload> payload = ArgumentCaptor.forClass(HitlResumePayload.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), payload.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("chat-expired"));
        assertEquals(HitlResumePayload.HitlDecision.EXPIRED, payload.getValue().decision());
        assertEquals(ActionStatus.EXPIRED, payload.getValue().actionStatus());
        assertEquals(ACTION_ID, payload.getValue().actionId());
        assertEquals("该申请草稿已过期，请重新生成。", payload.getValue().message());
        verify(threadGuard, never()).tryAcquire(anyString());
        verify(threadGuard, never()).release(anyString());
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
        order.verify(actionService).isAllowed(ADMIN_TOKEN, IDENTITY);
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
    void confirmedExpensePassesOnlyInternalExternalMarkerToFocusedCoordinator() {
        stubResumeDependencies();
        PendingAction expense = org.mockito.Mockito.mock(PendingAction.class);
        when(expense.actionId()).thenReturn(ACTION_ID);
        when(expense.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(expense.ownerUserId()).thenReturn(IDENTITY.userId());
        when(expense.conversationId()).thenReturn(CONVERSATION_ID);
        when(expense.agentExecutionId()).thenReturn("ex_" + "b".repeat(32));
        when(expense.hitlWaitId()).thenReturn("wait_" + "a".repeat(64));
        ActionExecutionResponse committed = new ActionExecutionResponse(
                ACTION_ID, BusinessActionType.EXPENSE_CLAIM, ActionStatus.SUCCEEDED,
                "EXP-20260827-000001", "已提交。", false, Instant.now(), "origin", "trace");
        ExternalWaitMarker externalWait = new ExternalWaitMarker(1, "EXPENSE_APPROVAL",
                ExternalWaitMarker.expectedWaitId("ex_" + "b".repeat(32), "EXP-20260827-000001"),
                "ex_" + "b".repeat(32), BusinessActionType.EXPENSE_CLAIM, "EXP-20260827-000001");
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(expense));
        when(actionService.confirm(anyString(), anyString(), anyString(), anyString(), anyString(), any()))
                .thenReturn(committed);
        doReturn(new PythonAgentResponse("waiting", "action", true, "business_action", "", List.of(),
                true, "resume-trace", null, List.of(), null, null, externalWait))
                .when(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), any(),
                        any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace"));

        assertSame(committed, coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "trace", IDENTITY));

        verify(externalApprovalCoordinator).registerExternalWaitAndDispatch(
                eq(expense), eq(committed), eq(externalWait), eq("trace"));
    }

    @Test
    void externalHandoffPreservesInterveningOwnerAfterConfirmReturns() {
        AgentRuntimeThreadExecutionGuard realGuard = new AgentRuntimeThreadExecutionGuard();
        coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService,
                realGuard, adminAccessService, externalApprovalCoordinator);
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(adminAccessService.isAdminIdentity(IDENTITY)).thenReturn(true);
        when(actionService.isAllowed(ADMIN_TOKEN, IDENTITY)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        PendingAction expense = org.mockito.Mockito.mock(PendingAction.class);
        when(expense.actionId()).thenReturn(ACTION_ID);
        when(expense.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(expense.ownerUserId()).thenReturn(IDENTITY.userId());
        when(expense.conversationId()).thenReturn(CONVERSATION_ID);
        when(expense.agentExecutionId()).thenReturn("ex_" + "b".repeat(32));
        when(expense.hitlWaitId()).thenReturn("wait_" + "a".repeat(64));
        ActionExecutionResponse committed = new ActionExecutionResponse(
                ACTION_ID, BusinessActionType.EXPENSE_CLAIM, ActionStatus.SUCCEEDED,
                "EXP-20260827-000001", "已提交。", false, Instant.now(), "origin", "trace");
        ExternalWaitMarker externalWait = new ExternalWaitMarker(1, "EXPENSE_APPROVAL",
                ExternalWaitMarker.expectedWaitId("ex_" + "b".repeat(32), "EXP-20260827-000001"),
                "ex_" + "b".repeat(32), BusinessActionType.EXPENSE_CLAIM, "EXP-20260827-000001");
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(expense));
        when(actionService.confirm(anyString(), anyString(), anyString(), anyString(), anyString(), any()))
                .thenReturn(committed);
        doReturn(new PythonAgentResponse("waiting", "action", true, "business_action", "", List.of(),
                true, "resume-trace", null, List.of(), null, null, externalWait))
                .when(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), any(),
                        any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace"));
        org.mockito.Mockito.doAnswer(invocation -> {
            // T1 已移交 guard；此时 T2 必须能够取得所有权。
            assertTrue(realGuard.tryAcquire(RUNTIME_THREAD_ID));
            return null;
        }).when(externalApprovalCoordinator).registerExternalWaitAndDispatch(
                eq(expense), eq(committed), eq(externalWait), eq("trace"));

        coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "trace", IDENTITY);

        // T1 的 finally 不得移除 T2 的所有权。
        assertFalse(realGuard.tryAcquire(RUNTIME_THREAD_ID));
        realGuard.release(RUNTIME_THREAD_ID);
        assertTrue(realGuard.tryAcquire(RUNTIME_THREAD_ID));
        realGuard.release(RUNTIME_THREAD_ID);
    }

    @Test
    void terminalReconciliationStillRunsAfterBusinessCapabilityRevocation() {
        stubResumeDependencies(false);
        PendingAction pending = pendingAction();
        ActionExecutionResponse committed = new ActionExecutionResponse(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.SUCCEEDED,
                "LR-202608-0001", "已提交。", false, Instant.now(), "origin", "trace");
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(actionService.confirm(anyString(), anyString(), anyString(), anyString(),
                anyString(), any())).thenReturn(committed);

        coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "trace", IDENTITY);

        verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq("trace"), any());
        ArgumentCaptor<HttpHeaders> headers = ArgumentCaptor.forClass(HttpHeaders.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), any(),
                headers.capture(), eq(PythonAgentResponse.class), eq("trace"));
        assertEquals("false", headers.getValue().getFirst("X-Allow-Business-Actions"));
        verify(threadGuard).release(RUNTIME_THREAD_ID);
    }

    @Test
    void confirmDoesNotEnterBusinessServiceWhenChatThreadGuardIsBusy() {
        PendingAction pending = pendingAction();
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(false);

        ActionException exception = assertThrows(ActionException.class, () -> coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, "trace", IDENTITY));

        assertEquals("ACTION_THREAD_BUSY", exception.errorCode());
        verify(actionService, never()).confirm(anyString(), anyString(), anyString(),
                anyString(), anyString(), any());
        verify(threadGuard, never()).release(anyString());
    }

    @Test
    void cancelDoesNotEnterBusinessServiceWhenChatThreadGuardIsBusy() {
        PendingAction pending = pendingAction();
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(false);

        ActionException exception = assertThrows(ActionException.class, () -> coordinator.cancel(
                ACTION_ID, "nonce", ADMIN_TOKEN, "trace", IDENTITY));

        assertEquals("ACTION_THREAD_BUSY", exception.errorCode());
        verify(actionService, never()).cancel(anyString(), anyString(), anyString(),
                anyString(), any());
        verify(threadGuard, never()).release(anyString());
    }

    @ParameterizedTest
    @MethodSource("deterministicRegistrationRejections")
    void deterministicRegistrationRejectionResumesCanonicalRejectedPayloadWithoutActionRow(
            String errorCode) {
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(adminAccessService.isAdminIdentity(IDENTITY)).thenReturn(true);
        when(actionService.isAllowed(ADMIN_TOKEN, IDENTITY)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        BusinessActionProposal proposal = registrationProposal();
        HitlWaitMarker wait = registrationWait();
        when(actionService.createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
                CONVERSATION_ID, wait.executionId(), wait.waitId()))
                .thenThrow(new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                        errorCode, "业务规则不满足", null, null));

        ActionException exception = assertThrows(ActionException.class, () -> coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));

        assertEquals(errorCode, exception.errorCode());
        verify(actionService).abandonMemoryAfterHitlRejection(
                IDENTITY, CONVERSATION_ID);
        ArgumentCaptor<HitlResumePayload> payload = ArgumentCaptor.forClass(HitlResumePayload.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), payload.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace"));
        assertEquals(HitlResumePayload.HitlDecision.REJECTED, payload.getValue().decision());
        assertNull(payload.getValue().actionId());
        assertNull(payload.getValue().requestId());
        assertEquals(ActionStatus.FAILED, payload.getValue().actionStatus());
        assertEquals(wait.waitId(), payload.getValue().waitId());
        assertEquals(wait.executionId(), payload.getValue().executionId());
        assertEquals(wait.actionType(), payload.getValue().actionType());
        assertEquals("申请未能完成，已安全拒绝。", payload.getValue().message());

        assertThrows(ActionException.class, () -> coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));
        ArgumentCaptor<HitlResumePayload> retryPayload = ArgumentCaptor.forClass(HitlResumePayload.class);
        verify(pythonAgentGateway, times(2)).post(
                eq("/agent/langgraph/hitl/resume"), retryPayload.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace"));
        assertEquals(payload.getValue(), retryPayload.getAllValues().get(1));
        verify(actionService, times(2)).abandonMemoryAfterHitlRejection(
                IDENTITY, CONVERSATION_ID);
        verifyNoInteractions(actions);
    }

    @ParameterizedTest
    @MethodSource("transientRegistrationRejections")
    void transientRegistrationRejectionDoesNotResumeOrCloseMemory(
            HttpStatus status, String errorCode) {
        BusinessActionProposal proposal = registrationProposal();
        HitlWaitMarker wait = registrationWait();
        when(actionService.createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
                CONVERSATION_ID, wait.executionId(), wait.waitId()))
                .thenThrow(new ActionException(status, errorCode, "暂时不可用", null, null));

        assertThrows(ActionException.class, () -> coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));

        verify(actionService, never()).abandonMemoryAfterHitlRejection(any(), anyString());
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void memoryFailureKeepsDeterministicRegistrationWaitingAndSkipsGraphResume() {
        BusinessActionProposal proposal = registrationProposal();
        HitlWaitMarker wait = registrationWait();
        when(actionService.createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
                CONVERSATION_ID, wait.executionId(), wait.waitId()))
                .thenThrow(new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                        "EXPENSE_AMOUNT_INVALID", "费用明细金额必须为正数。", null, null));
        doThrow(new RuntimeException("db unavailable")).when(actionService)
                .abandonMemoryAfterHitlRejection(IDENTITY, CONVERSATION_ID);

        RuntimeException exception = assertThrows(RuntimeException.class, () -> coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID));

        assertEquals("db unavailable", exception.getMessage());
        verify(actionService).abandonMemoryAfterHitlRejection(
                IDENTITY, CONVERSATION_ID);
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void expiredTerminalViewAndPayloadUseCanonicalTerminalSemantics() {
        when(threadIdService.generate(IDENTITY.userId(), CONVERSATION_ID))
                .thenReturn(RUNTIME_THREAD_ID);
        when(adminAccessService.isAdminIdentity(IDENTITY)).thenReturn(false);
        when(actionService.isAllowed(ADMIN_TOKEN, IDENTITY)).thenReturn(false);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 27));
        BusinessActionProposal proposal = registrationProposal();
        HitlWaitMarker wait = registrationWait();
        PendingAction expired = terminalAction(ActionStatus.EXPIRED);
        PendingActionView view = new PendingActionView(
                ACTION_ID, BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.EXPIRED,
                "年假申请", new AnnualLeaveSummary(
                        "E10001", LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1),
                        HalfDay.NONE, new BigDecimal("1.0"), "私事",
                        new BigDecimal("5.0"), new BigDecimal("4.0")),
                null, Instant.parse("2026-08-28T00:00:00Z"), false);
        when(actionService.createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
                CONVERSATION_ID, wait.executionId(), wait.waitId())).thenReturn(view);
        when(actions.findByHitlWaitId(wait.waitId())).thenReturn(Optional.of(expired));

        PendingActionView actual = coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID);

        assertFalse(actual.confirmationRequired());
        assertNull(actual.confirmationNonce());
        ArgumentCaptor<HitlResumePayload> payload = ArgumentCaptor.forClass(HitlResumePayload.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), payload.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace"));
        assertEquals(HitlResumePayload.HitlDecision.EXPIRED, payload.getValue().decision());
        assertEquals(ActionStatus.EXPIRED, payload.getValue().actionStatus());
        assertEquals("该申请草稿已过期，请重新生成。", payload.getValue().message());
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
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
                CONVERSATION_ID, wait.executionId(), wait.waitId())).thenReturn(view);
        when(actions.findByHitlWaitId(wait.waitId())).thenReturn(Optional.empty());

        PendingActionView actual = coordinator.registerWait(
                proposal, wait, "trace", ADMIN_TOKEN, IDENTITY, CONVERSATION_ID);

        assertSame(view, actual);
        verify(actionService).createHitlPending(
                proposal, "trace", ADMIN_TOKEN, IDENTITY,
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

    private static BusinessActionProposal registrationProposal() {
        return new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST,
                LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1), "私事", HalfDay.NONE);
    }

    private static HitlWaitMarker registrationWait() {
        return new HitlWaitMarker(
                1, "BUSINESS_ACTION_CONFIRMATION", "wait_" + "a".repeat(64),
                "ex_" + "b".repeat(32), BusinessActionType.ANNUAL_LEAVE_REQUEST);
    }

    private static Stream<String> deterministicRegistrationRejections() {
        return Stream.of(
                "BUSINESS_RULE_VIOLATION",
                "EXPENSE_ITEMS_REQUIRED",
                "EXPENSE_AMOUNT_INVALID",
                "EXPENSE_INVOICES_REQUIRED");
    }

    private static Stream<Arguments> transientRegistrationRejections() {
        return Stream.of(
                Arguments.of(HttpStatus.SERVICE_UNAVAILABLE, "ACTION_CAPACITY_EXCEEDED"),
                Arguments.of(HttpStatus.CONFLICT, "ACTION_CONVERSATION_IN_PROGRESS"),
                Arguments.of(HttpStatus.FORBIDDEN, "ADMIN_REQUIRED"));
    }

    private static PendingAction terminalAction(ActionStatus status) {
        PendingAction action = org.mockito.Mockito.mock(PendingAction.class);
        when(action.actionId()).thenReturn(ACTION_ID);
        when(action.actionType()).thenReturn(BusinessActionType.ANNUAL_LEAVE_REQUEST);
        when(action.ownerUserId()).thenReturn(IDENTITY.userId());
        when(action.conversationId()).thenReturn(CONVERSATION_ID);
        when(action.agentExecutionId()).thenReturn("ex_" + "b".repeat(32));
        when(action.hitlWaitId()).thenReturn("wait_" + "a".repeat(64));
        when(action.status()).thenReturn(status);
        when(action.requestId()).thenReturn(null);
        return action;
    }
}
