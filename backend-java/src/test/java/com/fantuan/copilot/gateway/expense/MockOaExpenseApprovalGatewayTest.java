package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseClaim;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestTemplate;

import java.math.BigDecimal;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class MockOaExpenseApprovalGatewayTest {
    private final RestTemplate restTemplate = mock(RestTemplate.class);
    private final MockOaExpenseApprovalGateway gateway = new MockOaExpenseApprovalGateway(
            restTemplate, true, "http://mock-oa:8010/");

    @Test
    void submitAcceptsTerminalReplayStatus() {
        ExpenseClaim claim = claim();
        when(restTemplate.postForObject(
                org.mockito.ArgumentMatchers.eq("http://mock-oa:8010/api/expense-approvals"),
                org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.eq(ExternalApprovalSubmissionResult.class)))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "APPROVED"));

        ExternalApprovalSubmissionResult result = gateway.submit(claim);

        assertEquals("OA-EXP-1", result.requestId());
        assertEquals("APPROVED", result.status());
    }

    @Test
    void getStatusReadsAuthoritativeCurrentState() {
        when(restTemplate.getForObject(
                "http://mock-oa:8010/api/expense-approvals/{requestId}",
                ExternalApprovalSubmissionResult.class, "OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "REJECTED"));

        ExternalApprovalSubmissionResult result = gateway.getStatus("OA-EXP-1");

        assertEquals("REJECTED", result.status());
    }

    @Test
    void getStatusRejectsMismatchedProviderRequestId() {
        when(restTemplate.getForObject(
                "http://mock-oa:8010/api/expense-approvals/{requestId}",
                ExternalApprovalSubmissionResult.class, "OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-OTHER", "APPROVED"));

        assertThrows(ExternalApprovalSubmissionException.class,
                () -> gateway.getStatus("OA-EXP-1"));
    }

    @Test
    void unsupportedStatusIsRejected() {
        when(restTemplate.getForObject(
                "http://mock-oa:8010/api/expense-approvals/{requestId}",
                ExternalApprovalSubmissionResult.class, "OA-EXP-1"))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-1", "PAID"));

        assertThrows(ExternalApprovalSubmissionException.class,
                () -> gateway.getStatus("OA-EXP-1"));
    }

    private ExpenseClaim claim() {
        return new ExpenseClaim("EXP-1", "act-1", "E10001", "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"),
                com.fantuan.copilot.model.action.ExpenseStatus.SUBMITTED,
                Instant.now(), Instant.now(), null, null, "wait-1");
    }
}
