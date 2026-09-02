package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ExternalResumePayload;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.http.HttpHeaders;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ExpenseExternalResumeCoordinatorTest {
    private static final String EXPENSE_ID = "EXP-20260828-000001";
    private static final String ACTION_ID = "act-expense-1";
    private static final String EMPLOYEE_ID = "E10001";
    private static final String OWNER_ID = "user-1";
    private static final String CONVERSATION_ID = "conversation-1";
    private static final String EXECUTION_ID = "ex_" + "a".repeat(32);
    private static final String RUNTIME_THREAD_ID = "rt_" + "b".repeat(64);
    private static final Instant NOW = Instant.parse("2026-08-28T08:00:00Z");
    private static final String EXPECTED_WAIT_ID =
            com.fantuan.copilot.dto.action.ExternalWaitMarker.expectedWaitId(EXECUTION_ID, EXPENSE_ID);

    @Mock ExpenseClaimRepository claims;
    @Mock PendingActionRepository actions;
    @Mock PythonAgentGateway pythonAgentGateway;
    @Mock BusinessActionService actionService;
    @Mock AgentRuntimeThreadIdService threadIdService;
    @Mock AgentRuntimeThreadExecutionGuard threadGuard;
    @Mock TaskRuntimeService taskRuntimeService;
    private ExpenseExternalResumeCoordinator coordinator;

    @BeforeEach
    void setUp() {
        coordinator = new ExpenseExternalResumeCoordinator(
                claims, actions, pythonAgentGateway, actionService, threadIdService,
                threadGuard, new TransactionTemplate(new NoopTransactionManager()),
                60000, Clock.fixed(NOW, ZoneOffset.UTC), taskRuntimeService);
        lenient().when(threadIdService.generate(OWNER_ID, CONVERSATION_ID)).thenReturn(RUNTIME_THREAD_ID);
        lenient().when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(true);
        lenient().when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 28));
    }

    @Test
    void approvedClaimSendsExactPayloadAndMarksCompletion() {
        PendingAction action = validAction();
        ExpenseClaim claim = claim(ExpenseStatus.APPROVED, null);
        stubValid(claim, action);
        when(pythonAgentGateway.post(eq("/agent/langgraph/external/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(successResponse());

        coordinator.tryResume(EXPENSE_ID);

        ExternalResumePayload payload = capturedPayload();
        assertEquals(1, payload.schemaVersion());
        assertEquals(EXPECTED_WAIT_ID, payload.waitId());
        assertEquals(EXECUTION_ID, payload.executionId());
        assertEquals(BusinessActionType.EXPENSE_CLAIM, payload.actionType());
        assertEquals(EXPENSE_ID, payload.requestId());
        assertEquals(ExternalResumePayload.Decision.APPROVED, payload.decision());
        assertEquals(payload.decision(), payload.status());
        assertEquals("报销申请已通过外部审批。", payload.message());
        verify(claims).markExternalResumeCompleted(eq(EXPENSE_ID), any(Instant.class));
    }

    @Test
    void rejectedClaimUsesCanonicalRejectedPayload() {
        PendingAction action = validAction();
        ExpenseClaim claim = claim(ExpenseStatus.REJECTED, null);
        stubValid(claim, action);
        when(pythonAgentGateway.post(eq("/agent/langgraph/external/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(successResponse());

        coordinator.tryResume(EXPENSE_ID);

        ExternalResumePayload payload = capturedPayload();
        assertEquals(ExternalResumePayload.Decision.REJECTED, payload.decision());
        assertEquals("报销申请已被外部审批拒绝。", payload.message());
        assertEquals(EXPENSE_ID, payload.requestId());
    }

    @Test
    void correlationMismatchNeverCallsPython() {
        PendingAction action = validAction();
        ExpenseClaim mismatched = claimWith(EXPENSE_ID, "different-action", EMPLOYEE_ID,
                EXPECTED_WAIT_ID, ExpenseStatus.APPROVED);
        when(claims.findByExpenseId(EXPENSE_ID)).thenReturn(Optional.of(
                mismatched));
        when(actions.find("different-action")).thenReturn(Optional.of(action));
        coordinator.tryResume(EXPENSE_ID);

        verify(claims, never()).tryMarkExternalResumeAttempt(anyString(), any(), any());
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void waitIdRecomputationMismatchNeverCallsPython() {
        PendingAction action = validAction();
        ExpenseClaim claim = claimWith(EXPENSE_ID, ACTION_ID, EMPLOYEE_ID,
                "extwait_" + "c".repeat(64), ExpenseStatus.APPROVED);
        stubValid(claim, action);

        coordinator.tryResume(EXPENSE_ID);

        verify(claims, never()).tryMarkExternalResumeAttempt(anyString(), any(), any());
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void employeeMismatchNeverCallsPython() {
        PendingAction action = validAction();
        ExpenseClaim claim = claimWith(EXPENSE_ID, ACTION_ID, "E99999", EXPECTED_WAIT_ID,
                ExpenseStatus.APPROVED);
        stubValid(claim, action);

        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void actionRequestIdMismatchNeverCallsPython() {
        PendingAction action = validAction();
        when(action.requestId()).thenReturn("EXP-OTHER");
        ExpenseClaim claim = claim(ExpenseStatus.APPROVED, null);
        stubValid(claim, action);

        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void actionNotSucceededNeverCallsPython() {
        PendingAction action = validAction();
        when(action.status()).thenReturn(ActionStatus.FAILED);
        stubValid(claim(ExpenseStatus.APPROVED, null), action);

        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void missingOwnerCorrelationNeverCallsPython() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(action.ownerUserId()).thenReturn(null);

        assertNoPythonResume();
    }

    @Test
    void missingConversationCorrelationNeverCallsPython() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(action.conversationId()).thenReturn(null);

        assertNoPythonResume();
    }

    @Test
    void missingExecutionCorrelationNeverCallsPython() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(action.agentExecutionId()).thenReturn(null);

        assertNoPythonResume();
    }

    @Test
    void missingHitlCorrelationNeverCallsPython() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(action.hitlWaitId()).thenReturn(null);

        assertNoPythonResume();
    }

    private void assertNoPythonResume() {
        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void trustedRuntimeHeadersComeOnlyFromPersistedActionAndDisableCapabilities() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(pythonAgentGateway.post(eq("/agent/langgraph/external/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString()))
                .thenReturn(successResponse());
        ArgumentCaptor<HttpHeaders> headers = ArgumentCaptor.forClass(HttpHeaders.class);

        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway).post(eq("/agent/langgraph/external/resume"), any(),
                headers.capture(), eq(PythonAgentResponse.class), anyString());
        assertEquals(RUNTIME_THREAD_ID, headers.getValue().getFirst("X-Agent-Thread-Id"));
        assertEquals(EMPLOYEE_ID, headers.getValue().getFirst("X-Employee-Id"));
        assertEquals(CONVERSATION_ID, headers.getValue().getFirst("X-Conversation-Id"));
        assertEquals("false", headers.getValue().getFirst("X-Allow-Eval"));
        assertEquals("false", headers.getValue().getFirst("X-Allow-Business-Actions"));
        assertEquals("2026-08-28", headers.getValue().getFirst("X-Business-Date"));
    }

    @Test
    void pythonUnavailableLeavesTerminalClaimAndCompletionMarkerUnchanged() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(pythonAgentGateway.post(eq("/agent/langgraph/external/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString()))
                .thenThrow(new RuntimeException("python unavailable"));

        coordinator.tryResume(EXPENSE_ID);

        verify(claims, never()).markExternalResumeCompleted(anyString(), any());
        verify(claims, never()).applyExternalApprovalStatus(anyString(), any());
    }

    @Test
    void completedClaimDoesNotMakeAnotherAttempt() {
        when(claims.findByExpenseId(EXPENSE_ID)).thenReturn(Optional.of(
                claim(ExpenseStatus.APPROVED, NOW.minusSeconds(1))));

        coordinator.tryResume(EXPENSE_ID);

        verify(actions, never()).find(anyString());
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void busyThreadSkipsPythonAndLeavesRowRetryable() {
        PendingAction action = validAction();
        stubValid(claim(ExpenseStatus.APPROVED, null), action);
        when(threadGuard.tryAcquire(RUNTIME_THREAD_ID)).thenReturn(false);

        coordinator.tryResume(EXPENSE_ID);

        verify(claims).tryMarkExternalResumeAttempt(eq(EXPENSE_ID), any(), any());
        verify(pythonAgentGateway, never()).post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), anyString());
        verify(claims, never()).markExternalResumeCompleted(anyString(), any());
    }

    @Test
    void responseLossThenRetryReconstructsIdenticalPayloadAndCompletes() {
        PendingAction action = validAction();
        ExpenseClaim claim = claim(ExpenseStatus.APPROVED, null);
        stubValid(claim, action);
        when(claims.tryMarkExternalResumeAttempt(anyString(), any(), any())).thenReturn(true);
        when(pythonAgentGateway.post(eq("/agent/langgraph/external/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString()))
                .thenThrow(new RuntimeException("response lost"))
                .thenReturn(successResponse());
        ArgumentCaptor<ExternalResumePayload> payloads =
                ArgumentCaptor.forClass(ExternalResumePayload.class);

        coordinator.tryResume(EXPENSE_ID);
        coordinator.tryResume(EXPENSE_ID);

        verify(pythonAgentGateway, org.mockito.Mockito.times(2)).post(
                eq("/agent/langgraph/external/resume"), payloads.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());
        assertEquals(payloads.getAllValues().get(0), payloads.getAllValues().get(1));
        assertEquals("报销申请已通过外部审批。", payloads.getAllValues().get(1).message());
        verify(claims).markExternalResumeCompleted(eq(EXPENSE_ID), any(Instant.class));
    }

    private void stubValid(ExpenseClaim claim, PendingAction action) {
        lenient().when(claims.findByExpenseId(EXPENSE_ID)).thenReturn(Optional.of(claim));
        lenient().when(actions.find(claim.sourceActionId())).thenReturn(Optional.of(action));
        lenient().when(claims.tryMarkExternalResumeAttempt(eq(EXPENSE_ID), any(), any())).thenReturn(true);
    }

    private PendingAction validAction() {
        PendingAction action = org.mockito.Mockito.mock(PendingAction.class);
        lenient().when(action.actionId()).thenReturn(ACTION_ID);
        lenient().when(action.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        lenient().when(action.status()).thenReturn(ActionStatus.SUCCEEDED);
        lenient().when(action.requestId()).thenReturn(EXPENSE_ID);
        lenient().when(action.employeeId()).thenReturn(EMPLOYEE_ID);
        lenient().when(action.ownerUserId()).thenReturn(OWNER_ID);
        lenient().when(action.conversationId()).thenReturn(CONVERSATION_ID);
        lenient().when(action.agentExecutionId()).thenReturn(EXECUTION_ID);
        lenient().when(action.hitlWaitId()).thenReturn("wait_" + "d".repeat(64));
        return action;
    }

    private ExpenseClaim claim(ExpenseStatus status, Instant completedAt) {
        return claimWith(EXPENSE_ID, ACTION_ID, EMPLOYEE_ID, EXPECTED_WAIT_ID, status,
                completedAt);
    }

    private ExpenseClaim claimWith(String expenseId, String sourceActionId, String employeeId,
                                   String externalWaitId, ExpenseStatus status) {
        return claimWith(expenseId, sourceActionId, employeeId, externalWaitId, status, null);
    }

    private ExpenseClaim claimWith(String expenseId, String sourceActionId, String employeeId,
                                   String externalWaitId, ExpenseStatus status,
                                   Instant completedAt) {
        return new ExpenseClaim(expenseId, sourceActionId, employeeId, "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"), status, NOW, NOW,
                "MOCK_OA", "OA-EXP-1", externalWaitId, null, null, completedAt);
    }

    private ExternalResumePayload capturedPayload() {
        ArgumentCaptor<ExternalResumePayload> payload =
                ArgumentCaptor.forClass(ExternalResumePayload.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/external/resume"), payload.capture(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());
        return payload.getValue();
    }

    private PythonAgentResponse successResponse() {
        return new PythonAgentResponse("done", "action", true, "business_action", "",
                List.of(), true, "python-trace", null, List.of(), null);
    }

    private static final class NoopTransactionManager implements PlatformTransactionManager {
        @Override public TransactionStatus getTransaction(TransactionDefinition definition) {
            return new SimpleTransactionStatus();
        }

        @Override public void commit(TransactionStatus status) { }

        @Override public void rollback(TransactionStatus status) { }
    }
}
