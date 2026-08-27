package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ExpenseExternalApprovalCoordinatorTest {
    private static final String EXECUTION = "ex_" + "a".repeat(32);
    private static final String EXPENSE_ID = "EXP-20260827-000001";

    @Mock ExpenseClaimRepository claims;
    @Mock ExpenseApprovalGateway gateway;
    @Mock PendingAction action;
    private ExpenseExternalApprovalCoordinator coordinator;

    @BeforeEach
    void setUp() {
        coordinator = new ExpenseExternalApprovalCoordinator(claims, gateway,
                new TransactionTemplate(new NoopTransactionManager()));
    }

    @Test
    void validMarkerBindsThenSubmitsAndMovesToWaitingApproval() {
        ExpenseClaim claim = claim();
        ExternalWaitMarker marker = marker(EXECUTION, EXPENSE_ID);
        stubExpenseAction();
        when(claims.findByExpenseId(EXPENSE_ID)).thenReturn(Optional.of(claim));
        when(gateway.submit(claim)).thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "PENDING"));

        coordinator.registerExternalWaitAndDispatch(action, success(), marker, "trace");

        verify(claims).bindExternalWait(EXPENSE_ID, marker.waitId());
        verify(gateway).submit(claim);
        verify(claims).bindExternalRequest(EXPENSE_ID, "MOCK_OA", "OA-EXP-1");
    }

    @Test
    void mismatchedExecutionFailsClosedBeforeAnyPersistenceOrGatewayCall() {
        ExternalWaitMarker marker = marker("ex_" + "b".repeat(32), EXPENSE_ID);
        stubExpenseAction();

        coordinator.registerExternalWaitAndDispatch(action, success(), marker, "trace");

        verify(claims, never()).bindExternalWait(any(), any());
        verify(gateway, never()).submit(any());
    }

    @Test
    void outboundFailureKeepsBoundWaitAndDoesNotBindExternalRequest() {
        ExpenseClaim claim = claim();
        ExternalWaitMarker marker = marker(EXECUTION, EXPENSE_ID);
        stubExpenseAction();
        when(claims.findByExpenseId(EXPENSE_ID)).thenReturn(Optional.of(claim));
        when(gateway.submit(claim)).thenThrow(new RuntimeException("timeout"));

        coordinator.registerExternalWaitAndDispatch(action, success(), marker, "trace");

        verify(claims).bindExternalWait(EXPENSE_ID, marker.waitId());
        verify(claims, never()).bindExternalRequest(any(), any(), any());
    }

    @Test
    void retryOnlySubmitsDurablePendingCandidates() {
        ExpenseClaim claim = claim();
        when(claims.findPendingExternalSubmissions(anyInt())).thenReturn(List.of(claim));
        when(gateway.submit(claim)).thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "PENDING"));

        coordinator.retryPendingSubmissions(20);

        verify(gateway).submit(claim);
        verify(claims).bindExternalRequest(EXPENSE_ID, "MOCK_OA", "OA-EXP-1");
    }

    private static ActionExecutionResponse success() {
        return new ActionExecutionResponse("act-expense", BusinessActionType.EXPENSE_CLAIM,
                ActionStatus.SUCCEEDED, EXPENSE_ID, "submitted", false,
                Instant.now(), "origin", "trace");
    }

    private static ExpenseClaim claim() {
        return new ExpenseClaim(EXPENSE_ID, "act-expense", "E10001", "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"), ExpenseStatus.SUBMITTED,
                Instant.now(), Instant.now(), null, null, "extwait_existing");
    }

    private static ExternalWaitMarker marker(String execution, String expenseId) {
        return new ExternalWaitMarker(1, "EXPENSE_APPROVAL",
                ExternalWaitMarker.expectedWaitId(execution, expenseId), execution,
                BusinessActionType.EXPENSE_CLAIM, expenseId);
    }

    private void stubExpenseAction() {
        when(action.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(action.agentExecutionId()).thenReturn(EXECUTION);
    }

    private static final class NoopTransactionManager implements PlatformTransactionManager {
        @Override public TransactionStatus getTransaction(TransactionDefinition definition) {
            return new SimpleTransactionStatus();
        }
        @Override public void commit(TransactionStatus status) { }
        @Override public void rollback(TransactionStatus status) { }
    }
}
