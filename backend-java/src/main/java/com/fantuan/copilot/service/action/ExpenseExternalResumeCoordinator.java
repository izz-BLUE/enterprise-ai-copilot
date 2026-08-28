package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ExternalResumePayload;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
import com.fantuan.copilot.gateway.expense.MockOaExpenseApprovalGateway;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

import java.time.Clock;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * Delivers a Java-authoritative terminal ExpenseClaim result to the original
 * Python execution.  Delivery markers are retry bookkeeping only; Java's
 * ExpenseClaim status remains the business authority.
 */
@Service
public class ExpenseExternalResumeCoordinator {
    private static final Logger log = LoggerFactory.getLogger(ExpenseExternalResumeCoordinator.class);
    private static final String APPROVED_MESSAGE = "报销申请已通过外部审批。";
    private static final String REJECTED_MESSAGE = "报销申请已被外部审批拒绝。";

    private final ExpenseClaimRepository claims;
    private final PendingActionRepository actions;
    private final PythonAgentGateway pythonAgentGateway;
    private final BusinessActionService actionService;
    private final AgentRuntimeThreadIdService threadIdService;
    private final AgentRuntimeThreadExecutionGuard threadGuard;
    private final TransactionOperations transactions;
    private final long retryIntervalMillis;
    private final Clock clock;
    private final TaskRuntimeService taskRuntimeService;

    @Autowired
    public ExpenseExternalResumeCoordinator(
            ExpenseClaimRepository claims,
            PendingActionRepository actions,
            PythonAgentGateway pythonAgentGateway,
            BusinessActionService actionService,
            AgentRuntimeThreadIdService threadIdService,
            AgentRuntimeThreadExecutionGuard threadGuard,
            TransactionOperations transactions,
            @Value("${external.approval.resume.retry-interval-ms:60000}") long retryIntervalMillis,
            Clock clock,
            TaskRuntimeService taskRuntimeService) {
        this.claims = claims;
        this.actions = actions;
        this.pythonAgentGateway = pythonAgentGateway;
        this.actionService = actionService;
        this.threadIdService = threadIdService;
        this.threadGuard = threadGuard;
        this.transactions = transactions;
        this.retryIntervalMillis = Math.max(1L, retryIntervalMillis);
        this.clock = clock;
        this.taskRuntimeService = taskRuntimeService;
    }

    /** Compatibility constructor for focused unit tests. */
    public ExpenseExternalResumeCoordinator(
            ExpenseClaimRepository claims,
            PendingActionRepository actions,
            PythonAgentGateway pythonAgentGateway,
            BusinessActionService actionService,
            AgentRuntimeThreadIdService threadIdService,
            AgentRuntimeThreadExecutionGuard threadGuard,
            TransactionOperations transactions,
            long retryIntervalMillis,
            Clock clock) {
        this(claims, actions, pythonAgentGateway, actionService, threadIdService,
                threadGuard, transactions, retryIntervalMillis, clock, null);
    }

    /** Best-effort immediate or retry delivery for one local ExpenseClaim. */
    public void tryResume(String expenseId) {
        if (expenseId == null || expenseId.isBlank()) {
            return;
        }
        try {
            ExpenseClaim claim = claims.findByExpenseId(expenseId).orElse(null);
            if (claim == null || claim.externalResumeCompletedAt() != null) {
                return;
            }
            PendingAction action = actions.find(claim.sourceActionId()).orElse(null);
            if (isTaskRuntimeClaim(claim)) {
                if (claim.status() == ExpenseStatus.APPROVED
                        || claim.status() == ExpenseStatus.REJECTED) {
                    taskRuntimeService.markTerminalByAction(claim.sourceActionId(),
                            claim.status() == ExpenseStatus.APPROVED
                                    ? com.fantuan.copilot.model.task.TaskExecutionStatus.COMPLETED
                                    : com.fantuan.copilot.model.task.TaskExecutionStatus.FAILED);
                }
                return;
            }
            ExternalResumePayload payload = buildPayload(claim, action);
            if (payload == null) {
                log.warn("EXTERNAL_RESUME_CORRELATION_CONFLICT expenseIdPrefix={}",
                        BusinessActionService.auditRef(expenseId));
                return;
            }

            Instant attemptedAt = clock.instant();
            Instant cutoff = attemptedAt.minusMillis(retryIntervalMillis);
            boolean claimed = Boolean.TRUE.equals(transactions.execute(status ->
                    claims.tryMarkExternalResumeAttempt(expenseId, cutoff, attemptedAt)));
            if (!claimed) {
                return;
            }

            String runtimeThreadId = threadIdService.generate(
                    action.ownerUserId(), action.conversationId());
            if (!threadGuard.tryAcquire(runtimeThreadId)) {
                log.warn("EXTERNAL_RESUME_PENDING expenseIdPrefix={} reason=thread_busy",
                        BusinessActionService.auditRef(expenseId));
                return;
            }
            String traceId = UUID.randomUUID().toString();
            try {
                PythonAgentResponse response = pythonAgentGateway.post(
                        "/agent/langgraph/external/resume", payload,
                        trustedHeaders(action, runtimeThreadId),
                        PythonAgentResponse.class, traceId);
                if (response == null || !Boolean.TRUE.equals(response.success())) {
                    log.warn("EXTERNAL_RESUME_PENDING expenseIdPrefix={} reason=python_unsuccessful",
                            BusinessActionService.auditRef(expenseId));
                    return;
                }
                transactions.executeWithoutResult(ignored ->
                        claims.markExternalResumeCompleted(expenseId, clock.instant()));
            } catch (RuntimeException exception) {
                log.warn("EXTERNAL_RESUME_PENDING expenseIdPrefix={} errorType={}",
                        BusinessActionService.auditRef(expenseId),
                        exception.getClass().getSimpleName());
            } finally {
                threadGuard.release(runtimeThreadId);
            }
        } catch (RuntimeException exception) {
            log.warn("EXTERNAL_RESUME_PENDING expenseIdPrefix={} errorType={}",
                    BusinessActionService.auditRef(expenseId),
                    exception.getClass().getSimpleName());
        }
    }

    private HttpHeaders trustedHeaders(PendingAction action, String runtimeThreadId) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Agent-Thread-Id", runtimeThreadId);
        headers.set("X-Employee-Id", action.employeeId());
        headers.set("X-Conversation-Id", action.conversationId());
        headers.set("X-Allow-Eval", "false");
        headers.set("X-Allow-Business-Actions", "false");
        headers.set("X-Business-Date", actionService.businessDate().toString());
        return headers;
    }

    private ExternalResumePayload buildPayload(ExpenseClaim claim, PendingAction action) {
        if (!isValidCorrelation(claim, action)) {
            return null;
        }
        ExpenseStatus status = claim.status();
        ExternalResumePayload.Decision decision = status == ExpenseStatus.APPROVED
                ? ExternalResumePayload.Decision.APPROVED
                : ExternalResumePayload.Decision.REJECTED;
        return new ExternalResumePayload(
                1,
                claim.externalWaitId(),
                action.agentExecutionId(),
                BusinessActionType.EXPENSE_CLAIM,
                claim.expenseId(),
                decision,
                decision,
                status == ExpenseStatus.APPROVED ? APPROVED_MESSAGE : REJECTED_MESSAGE);
    }

    private boolean isValidCorrelation(ExpenseClaim claim, PendingAction action) {
        if (claim == null || action == null
                || (claim.status() != ExpenseStatus.APPROVED
                && claim.status() != ExpenseStatus.REJECTED)
                || !MockOaExpenseApprovalGateway.PROVIDER.equals(claim.externalProvider())
                || isBlank(claim.expenseId())
                || isBlank(claim.sourceActionId())
                || isBlank(claim.externalRequestId())
                || isBlank(claim.externalWaitId())
                || action.actionType() != BusinessActionType.EXPENSE_CLAIM
                || action.status() != ActionStatus.SUCCEEDED
                || !claim.sourceActionId().equals(action.actionId())
                || !claim.expenseId().equals(action.requestId())
                || isBlank(claim.employeeId())
                || isBlank(action.employeeId())
                || !Objects.equals(claim.employeeId(), action.employeeId())
                || isBlank(action.ownerUserId())
                || isBlank(action.conversationId())
                || isBlank(action.agentExecutionId())
                || isBlank(action.hitlWaitId())) {
            return false;
        }
        try {
            ExternalWaitMarker marker = new ExternalWaitMarker(
                    1, "EXPENSE_APPROVAL", claim.externalWaitId(),
                    action.agentExecutionId(), BusinessActionType.EXPENSE_CLAIM,
                    claim.expenseId());
            return marker.hasExpectedWaitId();
        } catch (RuntimeException exception) {
            return false;
        }
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private boolean isTaskRuntimeClaim(ExpenseClaim claim) {
        return taskRuntimeService != null && claim != null
                && taskRuntimeService.findByActionId(claim.sourceActionId()).isPresent();
    }

}
