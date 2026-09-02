package com.fantuan.copilot.service.action;

import com.fantuan.copilot.gateway.expense.ExpenseApprovalGateway;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionException;
import com.fantuan.copilot.gateway.expense.ExternalApprovalSubmissionResult;
import com.fantuan.copilot.gateway.expense.MockOaExpenseApprovalGateway;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

/** webhook 投递和 reconciliation 共用的 Mock OA 权威刷新。 */
@Service
public class ExpenseExternalApprovalStatusSyncService {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalApprovalStatusSyncService.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseApprovalGateway approvalGateway;
    private final TransactionOperations transactions;
    private final ExpenseExternalResumeCoordinator resumeCoordinator;
    private final TaskRuntimeService taskRuntimeService;

    @Autowired
    public ExpenseExternalApprovalStatusSyncService(ExpenseClaimRepository claims,
                                                    ExpenseApprovalGateway approvalGateway,
                                                    TransactionOperations transactions,
                                                    ExpenseExternalResumeCoordinator resumeCoordinator,
                                                    TaskRuntimeService taskRuntimeService) {
        this.claims = claims;
        this.approvalGateway = approvalGateway;
        this.transactions = transactions;
        this.resumeCoordinator = resumeCoordinator;
        this.taskRuntimeService = taskRuntimeService;
    }

    public void sync(String externalRequestId) {
        if (externalRequestId == null || externalRequestId.isBlank()) {
            return;
        }
        ExpenseClaim claim = claims.findByExternalRequestId(externalRequestId).orElse(null);
        if (claim == null || !MockOaExpenseApprovalGateway.PROVIDER.equals(claim.externalProvider())) {
            log.warn("Ignoring Mock OA status refresh for unknown local requestIdPrefix={}",
                    BusinessActionService.auditRef(externalRequestId));
            return;
        }

        ExternalApprovalSubmissionResult authoritative;
        try {
            authoritative = approvalGateway.getStatus(externalRequestId);
        } catch (ExternalApprovalSubmissionException exception) {
            throw new ExpenseExternalApprovalStatusSyncException(
                    "Mock OA authoritative status query failed", exception);
        }
        if (authoritative == null || !externalRequestId.equals(authoritative.requestId())
                || !authoritative.isSupportedStatus()) {
            throw new ExpenseExternalApprovalStatusSyncException(
                    "Mock OA returned invalid authoritative status");
        }
        if ("PENDING".equals(authoritative.status())) {
            return;
        }

        ExpenseStatus terminal = "APPROVED".equals(authoritative.status())
                ? ExpenseStatus.APPROVED : ExpenseStatus.REJECTED;
        boolean taskRuntimeClaim = isTaskRuntimeClaim(claim);
        TaskExecutionStatus taskStatus = terminal == ExpenseStatus.APPROVED
                ? TaskExecutionStatus.COMPLETED : TaskExecutionStatus.FAILED;
        transactions.executeWithoutResult(ignored -> {
            claims.applyExternalApprovalStatus(externalRequestId, terminal);
            if (taskRuntimeClaim
                    && !taskRuntimeService.synchronizeBusinessStatus(
                    claim.sourceActionId(), taskStatus)) {
                throw new IllegalStateException("Task Runtime external terminal transition conflict");
            }
        });
        if (!taskRuntimeClaim) {
            // 上方的 ExpenseClaim 和 TaskExecution 终态更新完成后，才启动 legacy
            // Python continuation（继续执行）。
            resumeCoordinator.tryResume(claim.expenseId());
        }
    }

    private boolean isTaskRuntimeClaim(ExpenseClaim claim) {
        return claim != null
                && taskRuntimeService.findByActionId(claim.sourceActionId()).isPresent();
    }
}
