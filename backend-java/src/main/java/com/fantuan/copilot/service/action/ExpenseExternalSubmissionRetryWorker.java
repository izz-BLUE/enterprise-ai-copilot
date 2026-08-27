package com.fantuan.copilot.service.action;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Retries only durable Mock OA submission; it never polls approval status. */
@Component
public class ExpenseExternalSubmissionRetryWorker {
    private final ExpenseExternalApprovalCoordinator coordinator;
    private final boolean enabled;
    private final int batchSize;

    public ExpenseExternalSubmissionRetryWorker(ExpenseExternalApprovalCoordinator coordinator,
                                                @Value("${external.approval.retry.enabled:false}") boolean enabled,
                                                @Value("${external.approval.retry.batch-size:20}") int batchSize) {
        this.coordinator = coordinator;
        this.enabled = enabled;
        this.batchSize = Math.max(1, Math.min(batchSize, 100));
    }

    @Scheduled(fixedDelayString = "${external.approval.retry.fixed-delay-ms:30000}")
    public void runOnce() {
        if (enabled) {
            coordinator.retryPendingSubmissions(batchSize);
        }
    }
}
