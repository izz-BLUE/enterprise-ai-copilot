package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.gateway.expense.MockOaExpenseApprovalGateway;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

import java.util.List;

/**
 * Post-commit orchestration only: validates Java-owned correlation, records it,
 * then makes a best-effort external submission outside the local action transaction.
 */
@Service
public class ExpenseExternalApprovalCoordinator {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalApprovalCoordinator.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseApprovalGateway approvalGateway;
    private final TransactionOperations transactions;

    public ExpenseExternalApprovalCoordinator(ExpenseClaimRepository claims,
                                              ExpenseApprovalGateway approvalGateway,
                                              TransactionOperations transactions) {
        this.claims = claims;
        this.approvalGateway = approvalGateway;
        this.transactions = transactions;
    }

    public void registerExternalWaitAndDispatch(PendingAction action,
                                                ActionExecutionResponse response,
                                                ExternalWaitMarker marker,
                                                String traceId) {
        if (!isExpectedExpenseSuccess(action, response) || marker == null) {
            log.warn("[{}] EXTERNAL_CORRELATION_REJECTED actionIdPrefix={}", traceId,
                    BusinessActionService.auditRef(action == null ? null : action.actionId()));
            return;
        }
        try {
            transactions.executeWithoutResult(ignored -> bindValidatedWait(action, response, marker));
            dispatchByExpenseId(response.requestId(), traceId);
        } catch (RuntimeException exception) {
            // The action and Memory terminal state are authoritative and already committed.
            // Do not turn an external issue into a business-action failure or a graph resume.
            log.warn("[{}] EXTERNAL_SUBMISSION_PENDING expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(response.requestId()),
                    exception.getClass().getSimpleName());
        }
    }

    public void retryPendingSubmissions(int batchSize) {
        List<ExpenseClaim> candidates = claims.findPendingExternalSubmissions(Math.max(1, batchSize));
        for (ExpenseClaim claim : candidates) {
            dispatch(claim, "external-retry");
        }
    }

    private void bindValidatedWait(PendingAction action, ActionExecutionResponse response,
                                   ExternalWaitMarker marker) {
        ExpenseClaim claim = claims.findByExpenseId(response.requestId()).orElseThrow(
                () -> new IllegalStateException("Local expense claim missing"));
        if (marker.actionType() != BusinessActionType.EXPENSE_CLAIM
                || !marker.structurallyValid()
                || !marker.hasExpectedWaitId()
                || !response.requestId().equals(marker.requestId())
                || !claim.expenseId().equals(marker.requestId())
                || !action.agentExecutionId().equals(marker.executionId())) {
            throw new IllegalStateException("External wait marker correlation mismatch");
        }
        claims.bindExternalWait(claim.expenseId(), marker.waitId());
    }

    private boolean isExpectedExpenseSuccess(PendingAction action, ActionExecutionResponse response) {
        return action != null && response != null
                && action.actionType() == BusinessActionType.EXPENSE_CLAIM
                && response.type() == BusinessActionType.EXPENSE_CLAIM
                && response.status() == ActionStatus.SUCCEEDED
                && response.requestId() != null && !response.requestId().isBlank()
                && action.agentExecutionId() != null && !action.agentExecutionId().isBlank();
    }

    private void dispatchByExpenseId(String expenseId, String traceId) {
        ExpenseClaim claim = claims.findByExpenseId(expenseId).orElseThrow(
                () -> new IllegalStateException("Local expense claim missing"));
        dispatch(claim, traceId);
    }

    private void dispatch(ExpenseClaim claim, String traceId) {
        if (claim.status() != ExpenseStatus.SUBMITTED || claim.externalWaitId() == null
                || claim.externalRequestId() != null) {
            return;
        }
        try {
            ExternalApprovalSubmissionResult result = approvalGateway.submit(claim);
            if (result == null || result.requestId() == null || result.requestId().isBlank()
                    || !"PENDING".equals(result.status())) {
                throw new IllegalStateException("Unexpected external approval submission result");
            }
            transactions.executeWithoutResult(ignored -> claims.bindExternalRequest(
                    claim.expenseId(), MockOaExpenseApprovalGateway.PROVIDER, result.requestId()));
        } catch (RuntimeException exception) {
            log.warn("[{}] EXTERNAL_SUBMISSION_PENDING expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(claim.expenseId()),
                    exception.getClass().getSimpleName());
        }
    }
}
