package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionOperations;

import java.time.Clock;
import java.time.Instant;
import java.util.List;

/** Low-frequency fallback for approval notifications lost after external submission. */
@Component
public class ExpenseExternalApprovalReconciliationWorker {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalApprovalReconciliationWorker.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseExternalApprovalStatusSyncService statusSyncService;
    private final TransactionOperations transactions;
    private final boolean enabled;
    private final long intervalMillis;
    private final int batchSize;
    private final Clock clock;

    public ExpenseExternalApprovalReconciliationWorker(
            ExpenseClaimRepository claims,
            ExpenseExternalApprovalStatusSyncService statusSyncService,
            TransactionOperations transactions,
            @Value("${external.approval.reconciliation.enabled:false}") boolean enabled,
            @Value("${external.approval.reconciliation.interval-ms:60000}") long intervalMillis,
            @Value("${external.approval.reconciliation.batch-size:20}") int batchSize,
            Clock clock) {
        this.claims = claims;
        this.statusSyncService = statusSyncService;
        this.transactions = transactions;
        this.enabled = enabled;
        this.intervalMillis = Math.max(1L, intervalMillis);
        this.batchSize = Math.max(1, Math.min(batchSize, 100));
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${external.approval.reconciliation.interval-ms:60000}")
    public void runOnce() {
        if (!enabled) {
            return;
        }
        Instant now = clock.instant();
        Instant cutoff = now.minusMillis(intervalMillis);
        List<ExpenseClaim> candidates = claims.findExternalApprovalReconciliationCandidates(cutoff, batchSize);
        for (ExpenseClaim claim : candidates) {
            if (claim.externalRequestId() == null || claim.externalRequestId().isBlank()) {
                continue;
            }
            boolean claimed = Boolean.TRUE.equals(transactions.execute(status ->
                    claims.tryMarkExternalApprovalChecked(claim.expenseId(), claim.externalRequestId(),
                            cutoff, now)));
            if (!claimed) {
                continue;
            }
            try {
                statusSyncService.sync(claim.externalRequestId());
            } catch (RuntimeException exception) {
                log.warn("External approval reconciliation failed expenseIdPrefix={} errorType={}",
                        BusinessActionService.auditRef(claim.expenseId()),
                        exception.getClass().getSimpleName());
            }
        }
    }
}
