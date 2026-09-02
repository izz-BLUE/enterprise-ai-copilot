package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;
import com.fantuan.copilot.dto.action.HitlResumePayload;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.expense.ExpenseAuthoritativeRevalidationGateway;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "demo.auth.public-password=public-test-password",
        "demo.auth.interview-password=interview-test-password",
        "demo.auth.admin-password=admin-test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class ExpenseConfirmRevalidationIntegrationTest extends PostgresIntegrationTestBase {
    private static final String CONVERSATION_ID = "p6-confirm-revalidation";
    private static final VerifiedIdentity IDENTITY = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);

    @Autowired BusinessActionService actionService;
    @Autowired BusinessActionHitlCoordinator hitlCoordinator;
    @Autowired PendingActionRepository actions;
    @Autowired ExpenseClaimRepository claims;
    @Autowired AiTaskMemoryService memoryService;
    @Autowired JdbcTemplate jdbc;

    @MockitoBean ExpenseAuthoritativeRevalidationGateway revalidationGateway;
    @MockitoBean PythonAgentGateway pythonAgentGateway;

    @BeforeEach
    void resetDatabase() {
        reset(revalidationGateway, pythonAgentGateway);
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE expense_claim_number_seq RESTART WITH 1");
    }

    @Test
    void validFactsConfirmAndExternalBoundaryHasNoActiveTransaction() {
        AtomicReference<ExpenseRevalidationRequest> request = new AtomicReference<>();
        AtomicBoolean transactionActive = new AtomicBoolean(true);
        when(revalidationGateway.revalidate(any(), anyString())).thenAnswer(invocation -> {
            request.set(invocation.getArgument(0));
            transactionActive.set(TransactionSynchronizationManager.isActualTransactionActive());
            return validFacts();
        });

        PendingActionView pending = actionService.createPending(
                proposal(), "origin-p6-valid", null, IDENTITY, null);
        ActionExecutionResponse response = hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-valid", IDENTITY);

        assertEquals(ActionStatus.SUCCEEDED, response.status());
        assertFalse(transactionActive.get());
        assertEquals("E10001", request.get().employeeId());
        assertEquals("TRIP-20260818-001", request.get().tripId());
        assertEquals(List.of("INV-001", "INV-002"), request.get().invoiceIds());
        assertEquals(ExpenseStatus.SUBMITTED,
                claims.findByExpenseId(response.requestId()).orElseThrow().status());
    }

    @Test
    void staleTripCommitsFailedActionWithoutCreatingExpenseClaim() {
        when(revalidationGateway.revalidate(any(), anyString())).thenReturn(
                new ExpenseRevalidationResponse(1, true,
                        new ExpenseRevalidationResponse.TripFact(
                                "TRIP-20260818-001", "E10001",
                                "2026-08-18", "2026-08-20", "PENDING"),
                        validFacts().invoices(), null, null));
        PendingActionView pending = actionService.createPending(
                proposal(), "origin-p6-stale-trip", null, IDENTITY, null);

        assertThrows(ActionStaleException.class, () -> hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-stale-trip", IDENTITY));

        assertEquals(ActionStatus.FAILED, actions.find(pending.actionId()).orElseThrow().status());
        assertEquals("EXPENSE_TRIP_STALE", actions.find(pending.actionId()).orElseThrow().failureCode());
        assertEquals(0, claims.countBySourceActionId(pending.actionId()));
    }

    @Test
    void providerOutageKeepsPendingActionAndActiveMemoryAndRetrySucceeds() {
        memoryService.upsert("U10001", CONVERSATION_ID, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "active");
        when(revalidationGateway.revalidate(any(), anyString()))
                .thenThrow(new RuntimeException("Enterprise OA unavailable"))
                .thenReturn(validFacts());
        PendingActionView pending = actionService.createPending(
                proposal(), "origin-p6-outage", null, IDENTITY, CONVERSATION_ID);

        ExpenseRevalidationUnavailableException unavailable = assertThrows(
                ExpenseRevalidationUnavailableException.class, () -> hitlCoordinator.confirm(
                        pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                        null, "trace-p6-outage", IDENTITY));
        assertEquals("EXPENSE_REVALIDATION_UNAVAILABLE", unavailable.errorCode());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find("U10001", CONVERSATION_ID).orElseThrow().status());
        assertEquals(0, claims.countBySourceActionId(pending.actionId()));

        ActionExecutionResponse retry = hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-retry", IDENTITY);
        assertEquals(ActionStatus.SUCCEEDED, retry.status());
        assertEquals(1, claims.countBySourceActionId(pending.actionId()));
    }

    @Test
    void staleHitlConfirmationAbandonsMemoryAndSendsRejectedContinuation() {
        memoryService.upsert("U10001", CONVERSATION_ID, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "active");
        when(revalidationGateway.revalidate(any(), anyString()))
                .thenReturn(new ExpenseRevalidationResponse(
                        1, true, null, validFacts().invoices(), null, null));
        when(pythonAgentGateway.post(anyString(), any(), any(),
                any(), anyString())).thenReturn(null);
        HitlWaitMarker wait = new HitlWaitMarker(
                1, "BUSINESS_ACTION_CONFIRMATION", "wait_" + "a".repeat(64),
                "ex_" + "b".repeat(32), BusinessActionType.EXPENSE_CLAIM);
        PendingActionView pending = hitlCoordinator.registerWait(
                proposal(), wait, "origin-p6-hitl", null, IDENTITY, CONVERSATION_ID);

        assertThrows(ActionStaleException.class, () -> hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-hitl", IDENTITY));

        assertEquals(TaskStatus.ABANDONED,
                memoryService.find("U10001", CONVERSATION_ID).orElseThrow().status());
        assertEquals(ActionStatus.FAILED, actions.find(pending.actionId()).orElseThrow().status());
        verify(pythonAgentGateway).post(anyString(), any(HitlResumePayload.class),
                any(), any(), anyString());
        assertEquals(0, claims.countBySourceActionId(pending.actionId()));
    }

    @Test
    void failedStaleResumeIsRetriedByConfirmWithStableRejectedPayload() {
        memoryService.upsert("U10001", CONVERSATION_ID, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "active");
        when(revalidationGateway.revalidate(any(), anyString()))
                .thenReturn(new ExpenseRevalidationResponse(
                        1, true, null, validFacts().invoices(), null, null))
                .thenThrow(new RuntimeException("provider must not be consulted after FAILED"));
        List<HitlResumePayload> resumes = new ArrayList<>();
        when(pythonAgentGateway.post(anyString(), any(), any(), any(), anyString()))
                .thenAnswer(invocation -> {
                    resumes.add(invocation.getArgument(1, HitlResumePayload.class));
                    if (resumes.size() == 1) {
                        throw new RuntimeException("Python HITL resume unavailable");
                    }
                    return null;
                });
        HitlWaitMarker wait = new HitlWaitMarker(
                1, "BUSINESS_ACTION_CONFIRMATION", "wait_" + "a".repeat(64),
                "ex_" + "b".repeat(32), BusinessActionType.EXPENSE_CLAIM);
        PendingActionView pending = hitlCoordinator.registerWait(
                proposal(), wait, "origin-p6-retry", null, IDENTITY, CONVERSATION_ID);

        assertThrows(ActionStaleException.class, () -> hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-retry-first", IDENTITY));
        assertEquals(ActionStatus.FAILED, actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find("U10001", CONVERSATION_ID).orElseThrow().status());
        assertEquals(0, claims.countBySourceActionId(pending.actionId()));

        assertThrows(ActionException.class, () -> hitlCoordinator.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-p6-retry-second", IDENTITY));

        verify(revalidationGateway).revalidate(any(), anyString());
        assertEquals(2, resumes.size());
        assertEquals(resumes.get(0), resumes.get(1));
        assertEquals(HitlResumePayload.HitlDecision.REJECTED, resumes.get(1).decision());
        assertEquals(ActionStatus.FAILED, resumes.get(1).actionStatus());
        assertEquals(ActionStatus.FAILED, actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find("U10001", CONVERSATION_ID).orElseThrow().status());
        assertEquals(0, claims.countBySourceActionId(pending.actionId()));
    }

    private ExpenseActionProposal proposal() {
        return new ExpenseActionProposal(
                BusinessActionType.EXPENSE_CLAIM,
                "TRIP-20260818-001",
                List.of(
                        new ExpenseActionProposal.ExpenseItemPayload(
                                "HOTEL", new BigDecimal("1600"), "INV-001", "hotel"),
                        new ExpenseActionProposal.ExpenseItemPayload(
                                "TAXI", new BigDecimal("230"), "INV-002", "taxi")),
                new BigDecimal("1830"), new BigDecimal("1730"),
                "COST-DEFAULT", "expense", List.of("INV-001", "INV-002"), 2);
    }

    private static ExpenseRevalidationResponse validFacts() {
        return new ExpenseRevalidationResponse(1, true,
                new ExpenseRevalidationResponse.TripFact(
                        "TRIP-20260818-001", "E10001",
                        "2026-08-18", "2026-08-20", "APPROVED"),
                List.of(
                        new ExpenseRevalidationResponse.InvoiceFact(
                                "INV-001", true, false, new BigDecimal("1600"),
                                "HOTEL", true, null),
                        new ExpenseRevalidationResponse.InvoiceFact(
                                "INV-002", true, false, new BigDecimal("230"),
                                "TAXI", true, null)),
                null, null);
    }
}
