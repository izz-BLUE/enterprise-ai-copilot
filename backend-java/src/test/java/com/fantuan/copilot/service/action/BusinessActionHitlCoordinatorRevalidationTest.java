package com.fantuan.copilot.service.action;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BusinessActionHitlCoordinatorRevalidationTest {
    private static final String ACTION_ID = "act-expense-revalidate";
    private static final String ADMIN_TOKEN = "admin";
    private static final String TRACE_ID = "trace-confirm";
    private static final String THREAD_ID = "thread-1";
    private static final VerifiedIdentity IDENTITY = new VerifiedIdentity(
            "user-1", "user-1", "E10001", "User", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);

    @Mock BusinessActionService actionService;
    @Mock PendingActionRepository actions;
    @Mock PythonAgentGateway pythonAgentGateway;
    @Mock AgentRuntimeThreadIdService threadIdService;
    @Mock AgentRuntimeThreadExecutionGuard threadGuard;
    @Mock AdminAccessService adminAccessService;
    @Mock ExpenseExternalApprovalCoordinator externalApprovalCoordinator;
    @Mock ExpenseConfirmRevalidationService revalidation;

    private BusinessActionHitlCoordinator coordinator;

    @BeforeEach
    void setUp() {
        coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, revalidation);
        when(threadIdService.generate("user-1", "conversation-1")).thenReturn(THREAD_ID);
        when(threadGuard.tryAcquire(THREAD_ID)).thenReturn(true);
    }

    @Test
    void validExpenseRevalidationRunsBeforeTransactionalConfirm() {
        when(revalidation.revalidate(any(), eq(TRACE_ID))).thenReturn(null);
        PendingAction pending = expenseAction(ActionStatus.PENDING_CONFIRMATION);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));
        when(actionService.confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any())).thenReturn(success());

        ActionExecutionResponse response = coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY);

        assertEquals(ActionStatus.SUCCEEDED, response.status());
        InOrder order = inOrder(revalidation, actionService);
        order.verify(revalidation).revalidate(any(), eq(TRACE_ID));
        order.verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any());
        verify(threadGuard).release(THREAD_ID);
    }

    @Test
    void staleResultUsesJavaFinalizationAndNeverExecutesAction() {
        when(revalidation.revalidate(any(), eq(TRACE_ID))).thenReturn("EXPENSE_INVOICE_STALE");
        PendingAction pending = expenseAction(ActionStatus.PENDING_CONFIRMATION);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));

        ActionStaleException exception = assertThrows(ActionStaleException.class,
                () -> coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY));

        assertEquals("ACTION_STALE", exception.errorCode());
        verify(actionService).failStaleConfirmation(
                ACTION_ID, "nonce", ADMIN_TOKEN, TRACE_ID, IDENTITY.asDemoIdentity(),
                "EXPENSE_INVOICE_STALE");
        verify(actionService, never()).confirm(any(), any(), any(), any(), any(), any());
        verify(threadGuard).release(THREAD_ID);
    }

    @Test
    void providerUnavailableLeavesConfirmUntouchedAndReturnsRetryableError() {
        when(revalidation.revalidate(any(), eq(TRACE_ID))).thenThrow(
                new ExpenseRevalidationUnavailableException(
                        ACTION_ID, ActionStatus.PENDING_CONFIRMATION, new RuntimeException("down")));
        PendingAction pending = expenseAction(ActionStatus.PENDING_CONFIRMATION);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending));

        ExpenseRevalidationUnavailableException exception = assertThrows(
                ExpenseRevalidationUnavailableException.class,
                () -> coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY));

        assertEquals("EXPENSE_REVALIDATION_UNAVAILABLE", exception.errorCode());
        verify(actionService, never()).failStaleConfirmation(any(), any(), any(), any(), any(), any());
        verify(actionService, never()).confirm(any(), any(), any(), any(), any(), any());
        verify(threadGuard).release(THREAD_ID);
    }

    @Test
    void successfulReplaySkipsNewAuthoritativeRevalidation() {
        PendingAction succeeded = expenseAction(ActionStatus.SUCCEEDED);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(succeeded));
        when(actionService.confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any())).thenReturn(success());

        coordinator.confirm(ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY);

        verify(revalidation, never()).revalidate(any(), any());
        verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any());
    }

    @Test
    void pendingBeforeGuardButSucceededAfterGuardSkipsOaAndDelegatesReplay() {
        PendingAction pending = org.mockito.Mockito.mock(PendingAction.class);
        when(pending.ownerUserId()).thenReturn("user-1");
        when(pending.conversationId()).thenReturn("conversation-1");
        PendingAction succeeded = org.mockito.Mockito.mock(PendingAction.class);
        when(succeeded.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(succeeded.status()).thenReturn(ActionStatus.SUCCEEDED);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(pending), Optional.of(succeeded));
        when(actionService.confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any())).thenReturn(success().replayedFor(TRACE_ID));

        ActionExecutionResponse response = coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY);

        assertTrue(response.replayed());
        verify(revalidation, never()).revalidate(any(), any());
        verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any());
    }

    @ParameterizedTest
    @EnumSource(value = ActionStatus.class, names = {"FAILED", "CANCELLED", "EXPIRED", "PROCESSING"})
    void nonPendingExpenseStatesNeverCallAuthoritativeRevalidation(ActionStatus status) {
        PendingAction terminal = expenseAction(status);
        when(actions.find(ACTION_ID)).thenReturn(Optional.of(terminal));
        when(actionService.confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any())).thenThrow(new ActionException(
                HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT", "state", ACTION_ID, status));

        assertThrows(ActionException.class, () -> coordinator.confirm(
                ACTION_ID, "nonce", "idem", ADMIN_TOKEN, TRACE_ID, IDENTITY));

        verify(revalidation, never()).revalidate(any(), any());
        verify(actionService).confirm(eq(ACTION_ID), eq("nonce"), eq("idem"),
                eq(ADMIN_TOKEN), eq(TRACE_ID), any());
        verify(threadGuard).release(THREAD_ID);
    }

    private static PendingAction expenseAction(ActionStatus status) {
        PendingAction action = org.mockito.Mockito.mock(PendingAction.class);
        when(action.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(action.status()).thenReturn(status);
        when(action.ownerUserId()).thenReturn("user-1");
        when(action.conversationId()).thenReturn("conversation-1");
        return action;
    }

    private static ActionExecutionResponse success() {
        return new ActionExecutionResponse(ACTION_ID, BusinessActionType.EXPENSE_CLAIM,
                ActionStatus.SUCCEEDED, "EXP-1", "submitted", false,
                Instant.parse("2026-08-28T00:00:00Z"), "origin", TRACE_ID);
    }
}
