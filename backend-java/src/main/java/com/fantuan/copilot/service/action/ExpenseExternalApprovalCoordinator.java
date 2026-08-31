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
import org.springframework.beans.factory.annotation.Autowired;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * 仅负责提交后的编排：校验 Java 负责的 correlation、记录它，然后在本地 action
 * 事务之外尽力提交到外部系统。
 */
@Service
public class ExpenseExternalApprovalCoordinator {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalApprovalCoordinator.class);

    private final ExpenseClaimRepository claims;
    private final ExpenseApprovalGateway approvalGateway;
    private final TransactionOperations transactions;
    private final ExpenseExternalResumeCoordinator resumeCoordinator;
    private final com.fantuan.copilot.service.task.TaskRuntimeService taskRuntimeService;

    @Autowired
    public ExpenseExternalApprovalCoordinator(ExpenseClaimRepository claims,
                                              ExpenseApprovalGateway approvalGateway,
                                              TransactionOperations transactions,
                                              ExpenseExternalResumeCoordinator resumeCoordinator,
                                              com.fantuan.copilot.service.task.TaskRuntimeService taskRuntimeService) {
        this.claims = claims;
        this.approvalGateway = approvalGateway;
        this.transactions = transactions;
        this.resumeCoordinator = resumeCoordinator;
        this.taskRuntimeService = taskRuntimeService;
    }

    /** 兼容不包含 B3 wiring 的聚焦 B2a 单元测试的构造方法。 */
    public ExpenseExternalApprovalCoordinator(ExpenseClaimRepository claims,
                                              ExpenseApprovalGateway approvalGateway,
                                              TransactionOperations transactions) {
        this(claims, approvalGateway, transactions, null, null);
    }

    /** 兼容提供 legacy resume coordinator 的测试的构造方法。 */
    public ExpenseExternalApprovalCoordinator(ExpenseClaimRepository claims,
                                              ExpenseApprovalGateway approvalGateway,
                                              TransactionOperations transactions,
                                              ExpenseExternalResumeCoordinator resumeCoordinator) {
        this(claims, approvalGateway, transactions, resumeCoordinator, null);
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
        } catch (RuntimeException exception) {
            log.warn("[{}] EXTERNAL_CORRELATION_BINDING_FAILED expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(response.requestId()),
                    exception.getClass().getSimpleName());
            return;
        }
        // action 和 Memory 终态是权威状态，且已经提交。
        // 不要把外部问题转化为业务动作失败或 graph resume。
        try {
            dispatchByExpenseId(response.requestId(), traceId);
        } catch (RuntimeException exception) {
            log.warn("[{}] EXTERNAL_CORRELATION_LOOKUP_FAILED expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(response.requestId()),
                    exception.getClass().getSimpleName());
        }
    }

    /**
     * Task Runtime 外部 handoff。Python graph 在 Java HITL 确认后已经结束，
     * 因此该路径只在提交 OA 前绑定 Java 负责的 correlation value；永远不会创建
     * 或恢复 Python external interrupt。
     */
    public boolean registerTaskRuntimeAndDispatch(PendingAction action,
                                                  ActionExecutionResponse response,
                                                  String traceId) {
        if (!isExpectedTaskRuntimeExpenseSuccess(action, response)) {
            log.warn("[{}] TASK_RUNTIME_EXTERNAL_CORRELATION_REJECTED actionIdPrefix={}", traceId,
                    BusinessActionService.auditRef(action == null ? null : action.actionId()));
            return false;
        }
        try {
            transactions.executeWithoutResult(ignored -> bindTaskRuntimeWait(action, response));
        } catch (RuntimeException exception) {
            log.warn("[{}] TASK_RUNTIME_EXTERNAL_CORRELATION_BINDING_FAILED expenseIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(response.requestId()),
                    exception.getClass().getSimpleName());
            return false;
        }
        if (taskRuntimeService == null
                || !taskRuntimeService.findByActionId(action.actionId()).map(task ->
                task.status() == com.fantuan.copilot.model.task.TaskExecutionStatus.WAITING_EXTERNAL)
                .orElse(false)) {
            log.warn("[{}] TASK_RUNTIME_EXTERNAL_STATUS_BINDING_FAILED actionIdPrefix={}", traceId,
                    BusinessActionService.auditRef(action.actionId()));
            return false;
        }
        try {
            dispatchByExpenseId(response.requestId(), traceId);
        } catch (RuntimeException exception) {
            log.warn("[{}] TASK_RUNTIME_EXTERNAL_LOOKUP_FAILED expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(response.requestId()),
                    exception.getClass().getSimpleName());
        }
        return true;
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
                || action.actionId() == null
                || !Objects.equals(claim.sourceActionId(), action.actionId())
                || action.employeeId() == null
                || !Objects.equals(claim.employeeId(), action.employeeId())
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
                    || !result.isSupportedStatus()) {
                throw new IllegalStateException("Unexpected external approval submission result");
            }
            boolean taskRuntimeClaim = isTaskRuntimeClaim(claim);
            ExpenseStatus terminal = result.isTerminal()
                    ? terminalStatus(result.status()) : null;
            com.fantuan.copilot.model.task.TaskExecutionStatus taskStatus = terminal == null
                    ? null : terminal == ExpenseStatus.APPROVED
                    ? com.fantuan.copilot.model.task.TaskExecutionStatus.COMPLETED
                    : com.fantuan.copilot.model.task.TaskExecutionStatus.FAILED;
            transactions.executeWithoutResult(ignored -> {
                claims.bindExternalRequest(
                        claim.expenseId(), MockOaExpenseApprovalGateway.PROVIDER,
                        result.requestId());
                if (terminal != null) {
                    claims.applyExternalApprovalStatus(result.requestId(), terminal);
                    if (taskRuntimeClaim
                            && !taskRuntimeService.synchronizeBusinessStatus(
                            claim.sourceActionId(), taskStatus)) {
                        throw new IllegalStateException(
                                "Task Runtime external terminal transition conflict");
                    }
                }
            });
            if (terminal != null && !taskRuntimeClaim && resumeCoordinator != null) {
                // ExpenseClaim 终态事务已经提交；legacy Python continuation 是独立的
                // 尽力而为操作。
                resumeCoordinator.tryResume(claim.expenseId());
            }
        } catch (RuntimeException exception) {
            log.warn("[{}] EXTERNAL_SUBMISSION_PENDING expenseIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(claim.expenseId()),
                    exception.getClass().getSimpleName());
        }
    }

    private void bindTaskRuntimeWait(PendingAction action, ActionExecutionResponse response) {
        ExpenseClaim claim = claims.findByExpenseId(response.requestId()).orElseThrow(
                () -> new IllegalStateException("Local expense claim missing"));
        if (action.actionType() != BusinessActionType.EXPENSE_CLAIM
                || response.type() != BusinessActionType.EXPENSE_CLAIM
                || response.status() != ActionStatus.SUCCEEDED
                || !Objects.equals(claim.sourceActionId(), action.actionId())
                || !Objects.equals(claim.expenseId(), response.requestId())
                || !Objects.equals(claim.employeeId(), action.employeeId())) {
            throw new IllegalStateException("Task Runtime external correlation mismatch");
        }
        String waitId = claim.externalWaitId();
        if (waitId == null || waitId.isBlank()) {
            waitId = "task_wait_" + UUID.randomUUID().toString().replace("-", "");
        }
        claims.bindExternalWait(claim.expenseId(), waitId);
    }

    private boolean isExpectedTaskRuntimeExpenseSuccess(PendingAction action,
                                                        ActionExecutionResponse response) {
        return action != null && response != null
                && action.actionType() == BusinessActionType.EXPENSE_CLAIM
                && response.type() == BusinessActionType.EXPENSE_CLAIM
                && response.status() == ActionStatus.SUCCEEDED
                && response.requestId() != null && !response.requestId().isBlank()
                && action.actionId() != null && action.employeeId() != null;
    }

    private boolean isTaskRuntimeClaim(ExpenseClaim claim) {
        return taskRuntimeService != null && claim != null
                && taskRuntimeService.findByActionId(claim.sourceActionId()).isPresent();
    }

    private ExpenseStatus terminalStatus(String status) {
        return "APPROVED".equals(status) ? ExpenseStatus.APPROVED : ExpenseStatus.REJECTED;
    }
}
