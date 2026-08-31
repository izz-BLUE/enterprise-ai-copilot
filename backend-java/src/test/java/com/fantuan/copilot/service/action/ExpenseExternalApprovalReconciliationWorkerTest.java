package com.fantuan.copilot.service.action;

import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ExpenseExternalApprovalReconciliationWorkerTest {
    private static final Instant NOW = Instant.parse("2026-08-28T00:00:00Z");
    private static final Instant CUTOFF = NOW.minusSeconds(60);

    @Mock ExpenseClaimRepository claims;
    @Mock ExpenseApprovalGateway gateway;
    @Mock ExpenseExternalApprovalStatusSyncService statusSyncService;
    private ExpenseExternalApprovalReconciliationWorker worker;

    @BeforeEach
    void setUp() {
        worker = worker(60000, 20);
    }

    @Test
    void dueClaimIsMarkedBeforeAuthoritativeSync() {
        ExpenseClaim claim = claim("EXP-1", "OA-EXP-1");
        when(claims.findExternalApprovalReconciliationCandidates(CUTOFF, 20))
                .thenReturn(List.of(claim));
        when(claims.tryMarkExternalApprovalChecked("EXP-1", "OA-EXP-1", CUTOFF, NOW))
                .thenReturn(true);

        worker.runOnce();

        InOrder order = inOrder(claims, statusSyncService);
        order.verify(claims).tryMarkExternalApprovalChecked("EXP-1", "OA-EXP-1", CUTOFF, NOW);
        order.verify(statusSyncService).sync("OA-EXP-1");
    }

    @Test
    void lostWebhookWithApprovedAuthoritativeStatusTerminalizesClaim() {
        stubDueClaim();
        when(gateway.getStatus("OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "APPROVED"));
        worker = workerWithRealStatusSync();

        worker.runOnce();

        verify(claims).applyExternalApprovalStatus("OA-EXP-1", ExpenseStatus.APPROVED);
    }

    @Test
    void lostWebhookWithRejectedAuthoritativeStatusTerminalizesClaim() {
        stubDueClaim();
        when(gateway.getStatus("OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "REJECTED"));
        worker = workerWithRealStatusSync();

        worker.runOnce();

        verify(claims).applyExternalApprovalStatus("OA-EXP-1", ExpenseStatus.REJECTED);
    }

    @Test
    void pendingAuthoritativeStatusLeavesClaimWaiting() {
        stubDueClaim();
        when(gateway.getStatus("OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "PENDING"));
        worker = workerWithRealStatusSync();

        worker.runOnce();

        verify(claims, never()).applyExternalApprovalStatus(any(), any());
    }

    @Test
    void compareAndSetMissSkipsClaimSafely() {
        ExpenseClaim claim = claim("EXP-1", "OA-EXP-1");
        when(claims.findExternalApprovalReconciliationCandidates(CUTOFF, 20))
                .thenReturn(List.of(claim));
        when(claims.tryMarkExternalApprovalChecked("EXP-1", "OA-EXP-1", CUTOFF, NOW))
                .thenReturn(false);

        worker.runOnce();

        verify(statusSyncService, never()).sync("OA-EXP-1");
    }

    @Test
    void providerFailureDoesNotAbortTheRestOfTheBatch() {
        ExpenseClaim first = claim("EXP-1", "OA-EXP-1");
        ExpenseClaim second = claim("EXP-2", "OA-EXP-2");
        when(claims.findExternalApprovalReconciliationCandidates(CUTOFF, 20))
                .thenReturn(List.of(first, second));
        when(claims.tryMarkExternalApprovalChecked(any(), any(), eq(CUTOFF), eq(NOW)))
                .thenReturn(true);
        doThrow(new ExpenseExternalApprovalStatusSyncException("temporary outage"))
                .when(statusSyncService).sync("OA-EXP-1");

        worker.runOnce();

        verify(statusSyncService).sync("OA-EXP-1");
        verify(statusSyncService).sync("OA-EXP-2");
    }

    @Test
    void batchSizeIsClampedToOneHundred() {
        worker = worker(60000, 1000);

        worker.runOnce();

        verify(claims).findExternalApprovalReconciliationCandidates(CUTOFF, 100);
    }

    private ExpenseExternalApprovalReconciliationWorker worker(long intervalMillis,
                                                               int batchSize) {
        return new ExpenseExternalApprovalReconciliationWorker(
                claims, statusSyncService, new TransactionTemplate(new NoopTransactionManager()),
                intervalMillis, batchSize, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    private ExpenseExternalApprovalReconciliationWorker workerWithRealStatusSync() {
        TransactionTemplate transactions = new TransactionTemplate(new NoopTransactionManager());
        return new ExpenseExternalApprovalReconciliationWorker(
                claims, new ExpenseExternalApprovalStatusSyncService(claims, gateway, transactions),
                transactions, 60000, 20, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    private void stubDueClaim() {
        ExpenseClaim claim = claim("EXP-1", "OA-EXP-1");
        when(claims.findExternalApprovalReconciliationCandidates(CUTOFF, 20))
                .thenReturn(List.of(claim));
        when(claims.findByExternalRequestId("OA-EXP-1"))
                .thenReturn(Optional.of(claim));
        when(claims.tryMarkExternalApprovalChecked("EXP-1", "OA-EXP-1", CUTOFF, NOW))
                .thenReturn(true);
    }

    private ExpenseClaim claim(String expenseId, String requestId) {
        return new ExpenseClaim(expenseId, "act-" + expenseId, "E10001", "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"), ExpenseStatus.WAITING_APPROVAL,
                NOW.minusSeconds(3600), NOW.minusSeconds(3600), "MOCK_OA", requestId, "wait-" + expenseId);
    }

    private static final class NoopTransactionManager implements PlatformTransactionManager {
        @Override public TransactionStatus getTransaction(TransactionDefinition definition) {
            return new SimpleTransactionStatus();
        }

        @Override public void commit(TransactionStatus status) { }

        @Override public void rollback(TransactionStatus status) { }
    }
}
