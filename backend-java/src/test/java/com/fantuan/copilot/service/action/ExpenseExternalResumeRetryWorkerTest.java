package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;

import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ExpenseExternalResumeRetryWorkerTest {
    private static final Instant NOW = Instant.parse("2026-08-28T08:00:00Z");

    @Mock ExpenseClaimRepository claims;
    @Mock ExpenseExternalResumeCoordinator coordinator;
    private ExpenseExternalResumeRetryWorker worker;

    @BeforeEach
    void setUp() {
        worker = worker(60000, 20);
    }

    @Test
    void dueCandidatesAreDeliveredInReturnedFairOrder() {
        ExpenseClaim first = claim("EXP-1");
        ExpenseClaim second = claim("EXP-2");
        when(claims.findExternalResumeCandidates(NOW.minusSeconds(60), 20))
                .thenReturn(List.of(first, second));

        worker.runOnce();

        var order = org.mockito.Mockito.inOrder(coordinator);
        order.verify(coordinator).tryResume("EXP-1");
        order.verify(coordinator).tryResume("EXP-2");
    }

    @Test
    void oneCandidateFailureDoesNotAbortLaterCandidate() {
        ExpenseClaim first = claim("EXP-1");
        ExpenseClaim second = claim("EXP-2");
        when(claims.findExternalResumeCandidates(NOW.minusSeconds(60), 20))
                .thenReturn(List.of(first, second));
        doThrow(new RuntimeException("temporary failure")).when(coordinator).tryResume("EXP-1");

        worker.runOnce();

        verify(coordinator).tryResume("EXP-1");
        verify(coordinator).tryResume("EXP-2");
    }

    @Test
    void batchSizeIsClampedToOneHundred() {
        worker = worker(60000, 1000);

        worker.runOnce();

        verify(claims).findExternalResumeCandidates(NOW.minusSeconds(60), 100);
    }

    private ExpenseExternalResumeRetryWorker worker(long intervalMillis,
                                                     int batchSize) {
        return new ExpenseExternalResumeRetryWorker(
                claims, coordinator, intervalMillis, batchSize,
                Clock.fixed(NOW, ZoneOffset.UTC));
    }

    private ExpenseClaim claim(String expenseId) {
        return new ExpenseClaim(expenseId, "act-" + expenseId, "E10001", "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"), ExpenseStatus.APPROVED,
                NOW.minusSeconds(3600), NOW.minusSeconds(3600), "MOCK_OA", "OA-" + expenseId,
                "extwait_" + "a".repeat(64));
    }
}
