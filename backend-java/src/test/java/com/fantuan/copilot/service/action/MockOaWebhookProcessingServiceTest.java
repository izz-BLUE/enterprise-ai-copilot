package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.webhook.MockOaExpenseApprovalWebhook;
import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
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
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MockOaWebhookProcessingServiceTest {
    private static final String REQUEST_ID = "OA-EXP-1";

    @Mock ExpenseClaimRepository claims;
    @Mock ExpenseApprovalGateway gateway;
    @Mock ExpenseExternalResumeCoordinator resumeCoordinator;
    private MockOaWebhookProcessingService service;

    @BeforeEach
    void setUp() {
        ExpenseExternalApprovalStatusSyncService statusSyncService =
                new ExpenseExternalApprovalStatusSyncService(claims, gateway,
                        new TransactionTemplate(new NoopTransactionManager()), resumeCoordinator);
        service = new MockOaWebhookProcessingService(statusSyncService);
    }

    @Test
    void pendingAuthoritativeStatusDoesNotChangeLocalState() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "PENDING"));

        service.process(webhook());

        verify(claims, never()).applyExternalApprovalStatus(any(), any());
    }

    @Test
    void pendingNotificationAfterLocalTerminalDoesNotRegressState() {
        stubClaim(ExpenseStatus.APPROVED);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "PENDING"));

        service.process(webhook());

        verify(claims, never()).applyExternalApprovalStatus(any(), any());
    }

    @Test
    void authoritativeApprovedStatusMovesLocalClaimToApproved() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "APPROVED"));

        service.process(webhook());

        verify(claims).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.APPROVED);
    }

    @Test
    void authoritativeRejectedStatusMovesLocalClaimToRejected() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "REJECTED"));

        service.process(webhook());

        verify(claims).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.REJECTED);
    }

    @Test
    void terminalStatusCommitsBeforeBestEffortExternalResume() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "APPROVED"));

        service.process(webhook());

        var order = inOrder(claims, resumeCoordinator);
        order.verify(claims).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.APPROVED);
        order.verify(resumeCoordinator).tryResume("EXP-1");
    }

    @Test
    void duplicateApprovedNotificationDelegatesIdempotentTerminalTransition() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "APPROVED"));

        service.process(webhook());
        service.process(webhook());

        verify(claims, times(2)).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.APPROVED);
    }

    @Test
    void duplicateRejectedNotificationDelegatesIdempotentTerminalTransition() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "REJECTED"));

        service.process(webhook());
        service.process(webhook());

        verify(claims, times(2)).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.REJECTED);
    }

    @Test
    void anOlderNotificationCannotRegressAnAlreadyTerminalClaim() {
        stubClaim(ExpenseStatus.APPROVED);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult(REQUEST_ID, "APPROVED"));

        service.process(webhook());

        verify(claims).applyExternalApprovalStatus(REQUEST_ID, ExpenseStatus.APPROVED);
    }

    @Test
    void mismatchedAuthoritativeRequestIdFailsBeforeLocalMutation() {
        stubClaim(ExpenseStatus.WAITING_APPROVAL);
        when(gateway.getStatus(REQUEST_ID))
                .thenReturn(new ExternalApprovalSubmissionResult("OA-EXP-OTHER", "APPROVED"));

        assertThrows(MockOaWebhookProcessingException.class, () -> service.process(webhook()));

        verify(claims, never()).applyExternalApprovalStatus(any(), any());
    }

    @Test
    void unknownLocalRequestIsSafeNoOpWithoutProviderQuery() {
        when(claims.findByExternalRequestId(REQUEST_ID)).thenReturn(Optional.empty());

        service.process(webhook());

        verify(gateway, never()).getStatus(any());
        verify(claims, never()).applyExternalApprovalStatus(any(), any());
    }

    @Test
    void nonMockProviderIsSafeNoOpWithoutProviderQuery() {
        ExpenseClaim claim = claim(ExpenseStatus.WAITING_APPROVAL, "OTHER_PROVIDER");
        when(claims.findByExternalRequestId(REQUEST_ID)).thenReturn(Optional.of(claim));

        service.process(webhook());

        verify(gateway, never()).getStatus(any());
    }

    private void stubClaim(ExpenseStatus status) {
        when(claims.findByExternalRequestId(REQUEST_ID))
                .thenReturn(Optional.of(claim(status, "MOCK_OA")));
    }

    private ExpenseClaim claim(ExpenseStatus status, String provider) {
        return new ExpenseClaim("EXP-1", "act-1", "E10001", "TRIP-1", "COST-IT",
                new BigDecimal("100"), new BigDecimal("100"), status,
                Instant.now(), Instant.now(), provider, REQUEST_ID, "wait-1");
    }

    private MockOaExpenseApprovalWebhook webhook() {
        return new MockOaExpenseApprovalWebhook("evt-1",
                MockOaExpenseApprovalWebhook.EVENT_TYPE, REQUEST_ID);
    }

    private static final class NoopTransactionManager implements PlatformTransactionManager {
        @Override public TransactionStatus getTransaction(TransactionDefinition definition) {
            return new SimpleTransactionStatus();
        }

        @Override public void commit(TransactionStatus status) { }

        @Override public void rollback(TransactionStatus status) { }
    }
}
