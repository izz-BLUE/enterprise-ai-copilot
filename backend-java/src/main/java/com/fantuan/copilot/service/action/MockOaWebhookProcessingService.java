package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.webhook.MockOaExpenseApprovalWebhook;
import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionException;
import com.fantuan.copilot.gateway.expense.MockOaExpenseApprovalGateway;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

/** Treats the webhook as a refresh notification and Mock OA GET as the authority. */
@Service
public class MockOaWebhookProcessingService {
    private static final Logger log = LoggerFactory.getLogger(MockOaWebhookProcessingService.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseApprovalGateway approvalGateway;
    private final TransactionOperations transactions;

    public MockOaWebhookProcessingService(ExpenseClaimRepository claims,
                                           ExpenseApprovalGateway approvalGateway,
                                           TransactionOperations transactions) {
        this.claims = claims;
        this.approvalGateway = approvalGateway;
        this.transactions = transactions;
    }

    public void process(MockOaExpenseApprovalWebhook webhook) {
        ExpenseClaim claim = claims.findByExternalRequestId(webhook.requestId()).orElse(null);
        if (claim == null || !MockOaExpenseApprovalGateway.PROVIDER.equals(claim.externalProvider())) {
            log.warn("Ignoring authenticated Mock OA notification for unknown local requestIdPrefix={}",
                    BusinessActionService.auditRef(webhook.requestId()));
            return;
        }

        ExternalApprovalSubmissionResult authoritative;
        try {
            authoritative = approvalGateway.getStatus(webhook.requestId());
        } catch (ExternalApprovalSubmissionException exception) {
            throw new MockOaWebhookProcessingException("Mock OA authoritative status query failed", exception);
        }
        if (authoritative == null || !webhook.requestId().equals(authoritative.requestId())
                || !authoritative.isSupportedStatus()) {
            throw new MockOaWebhookProcessingException("Mock OA returned invalid authoritative status");
        }
        if ("PENDING".equals(authoritative.status())) {
            return;
        }

        ExpenseStatus terminal = "APPROVED".equals(authoritative.status())
                ? ExpenseStatus.APPROVED : ExpenseStatus.REJECTED;
        transactions.executeWithoutResult(ignored ->
                claims.applyExternalApprovalStatus(webhook.requestId(), terminal));
    }
}
