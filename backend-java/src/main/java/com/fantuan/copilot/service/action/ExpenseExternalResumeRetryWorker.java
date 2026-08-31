package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;
import java.util.List;

/** Retries only incomplete Java -> Python external resume delivery. */
@Component
public class ExpenseExternalResumeRetryWorker {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalResumeRetryWorker.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseExternalResumeCoordinator coordinator;
    private final long intervalMillis;
    private final int batchSize;
    private final Clock clock;

    public ExpenseExternalResumeRetryWorker(
            ExpenseClaimRepository claims,
            ExpenseExternalResumeCoordinator coordinator,
            @Value("${external.approval.resume.retry-interval-ms:60000}") long intervalMillis,
            @Value("${external.approval.resume.batch-size:20}") int batchSize,
            Clock clock) {
        this.claims = claims;
        this.coordinator = coordinator;
        this.intervalMillis = Math.max(1L, intervalMillis);
        this.batchSize = Math.max(1, Math.min(batchSize, 100));
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${external.approval.resume.retry-interval-ms:60000}")
    public void runOnce() {
        Instant now = clock.instant();
        List<ExpenseClaim> candidates = claims.findExternalResumeCandidates(
                now.minusMillis(intervalMillis), batchSize);
        for (ExpenseClaim claim : candidates) {
            try {
                coordinator.tryResume(claim.expenseId());
            } catch (RuntimeException exception) {
                // A single delivery issue must not starve later fair candidates.
                // The coordinator normally absorbs expected transport failures;
                // this boundary also protects the batch from unexpected errors.
                log.warn("EXTERNAL_RESUME_PENDING expenseIdPrefix={} errorType={}",
                        BusinessActionService.auditRef(claim.expenseId()),
                        exception.getClass().getSimpleName());
            }
        }
    }
}
