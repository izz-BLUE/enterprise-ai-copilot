package com.fantuan.copilot.service.action;

import org.junit.jupiter.api.Test;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class ExpenseExternalSubmissionRetryWorkerTest {
    private final ExpenseExternalApprovalCoordinator coordinator =
            mock(ExpenseExternalApprovalCoordinator.class);

    @Test
    void delegatesBoundedSubmissionRecovery() {
        new ExpenseExternalSubmissionRetryWorker(coordinator, 20).runOnce();

        verify(coordinator).retryPendingSubmissions(20);
    }

    @Test
    void clampsBatchSizeToOneHundred() {
        new ExpenseExternalSubmissionRetryWorker(coordinator, 1000).runOnce();

        verify(coordinator).retryPendingSubmissions(100);
    }

    @Test
    void keepsAtLeastOneRecoveryCandidate() {
        new ExpenseExternalSubmissionRetryWorker(coordinator, 0).runOnce();

        verify(coordinator).retryPendingSubmissions(1);
    }
}
