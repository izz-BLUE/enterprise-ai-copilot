package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlResumePayload;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * Coordinates Java's action authority with the Python checkpoint continuation.
 * BusinessActionService remains the only owner of state transitions and side
 * effects; this class only performs trusted routing, guard ownership and
 * best-effort graph reconciliation after the Java transaction has committed.
 */
@Service
public class BusinessActionHitlCoordinator {
    private static final Logger log = LoggerFactory.getLogger(BusinessActionHitlCoordinator.class);
    private static final String CANCELLED_MESSAGE = "申请草稿已取消。";
    private static final String EXPIRED_MESSAGE = "该申请草稿已过期，请重新生成。";
    private static final String REJECTED_MESSAGE = "申请未能完成，已安全拒绝。";

    private final BusinessActionService actionService;
    private final PendingActionRepository actions;
    private final PythonAgentGateway pythonAgentGateway;
    private final AgentRuntimeThreadIdService threadIdService;
    private final AgentRuntimeThreadExecutionGuard threadGuard;
    private final AdminAccessService adminAccessService;
    private final ExpenseExternalApprovalCoordinator externalApprovalCoordinator;

    public BusinessActionHitlCoordinator(BusinessActionService actionService,
                                         PendingActionRepository actions,
                                         PythonAgentGateway pythonAgentGateway,
                                         AgentRuntimeThreadIdService threadIdService,
                                         AgentRuntimeThreadExecutionGuard threadGuard,
                                         AdminAccessService adminAccessService,
                                         ExpenseExternalApprovalCoordinator externalApprovalCoordinator) {
        this.actionService = actionService;
        this.actions = actions;
        this.pythonAgentGateway = pythonAgentGateway;
        this.threadIdService = threadIdService;
        this.threadGuard = threadGuard;
        this.adminAccessService = adminAccessService;
        this.externalApprovalCoordinator = externalApprovalCoordinator;
    }

    /** Register the wait after Python has durably checkpointed the interrupt. */
    public PendingActionView registerWait(BusinessActionProposal proposal,
                                          HitlWaitMarker wait,
                                          String originTraceId,
                                          String presentedToken,
                                          VerifiedIdentity identity,
                                          String conversationId) {
        validateWaitAndProposal(proposal, wait);
        try {
            PendingActionView view = actionService.createHitlPending(
                    proposal, originTraceId, presentedToken, identity.asDemoIdentity(),
                    conversationId, wait.executionId(), wait.waitId());

            // A Java commit may have succeeded before the HTTP response was lost.
            // Reconcile that terminal row without creating a second action.
            Optional<PendingAction> terminal = actions.findByHitlWaitId(wait.waitId());
            if (terminal != null && terminal.isPresent() && isTerminal(terminal.get().status())) {
                tryResume(terminal.get(), identity, presentedToken, originTraceId);
            }
            return view;
        } catch (ActionException exception) {
            if (isDeterministicRegistrationRejection(exception)) {
                // No PendingAction exists on this path.  Close only Java's
                // existing ACTIVE task memory and reject the durable wait;
                // never manufacture a fake action row for correlation.
                // Memory is the Java-owned lifecycle predecessor of Graph
                // terminalization.  If it fails, keep the checkpoint waiting
                // so a retry can perform Memory -> Graph in that order.
                actionService.abandonMemoryAfterHitlRejection(
                        identity.asDemoIdentity(), conversationId);
                tryResumeRejected(wait, identity, conversationId,
                        presentedToken, originTraceId);
            }
            throw exception;
        }
    }

    public ActionExecutionResponse confirm(String actionId, String confirmationNonce,
                                           String idempotencyKey, String presentedToken,
                                           String traceId, VerifiedIdentity identity) {
        PendingAction routing = resolveRouting(actionId, presentedToken, identity);
        String guardKey = guardKey(routing, identity);
        acquireOrBusy(guardKey);
        try {
            try {
                ActionExecutionResponse response = actionService.confirm(
                        actionId, confirmationNonce, idempotencyKey, presentedToken,
                        traceId, identity.asDemoIdentity());
                reconcileAfterCommittedAction(actionId, routing, response,
                        identity, presentedToken, traceId, guardKey);
                return response;
            } catch (ActionExpiredAfterUpdateException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                        EXPIRED_MESSAGE, null);
                throw exception;
            } catch (ActionStaleException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.REJECTED, ActionStatus.FAILED,
                        REJECTED_MESSAGE, null);
                throw exception;
            } catch (ActionException exception) {
                if ("ACTION_EXPIRED".equals(exception.errorCode())) {
                    reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                            HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                            EXPIRED_MESSAGE, null);
                }
                throw exception;
            }
        } finally {
            threadGuard.release(guardKey);
        }
    }

    public ActionExecutionResponse cancel(String actionId, String confirmationNonce,
                                          String presentedToken, String traceId,
                                          VerifiedIdentity identity) {
        PendingAction routing = resolveRouting(actionId, presentedToken, identity);
        String guardKey = guardKey(routing, identity);
        acquireOrBusy(guardKey);
        try {
            try {
                ActionExecutionResponse response = actionService.cancel(
                        actionId, confirmationNonce, presentedToken, traceId,
                        identity.asDemoIdentity());
                reconcileAfterCommittedAction(actionId, routing, response,
                        identity, presentedToken, traceId, guardKey);
                return response;
            } catch (ActionExpiredAfterUpdateException exception) {
                reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                        HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                        EXPIRED_MESSAGE, null);
                throw exception;
            } catch (ActionException exception) {
                if ("ACTION_EXPIRED".equals(exception.errorCode())) {
                    reconcileTerminal(actionId, routing, identity, presentedToken, traceId,
                            HitlResumePayload.HitlDecision.EXPIRED, ActionStatus.EXPIRED,
                            EXPIRED_MESSAGE, null);
                }
                throw exception;
            }
        } finally {
            threadGuard.release(guardKey);
        }
    }

    private void validateWaitAndProposal(BusinessActionProposal proposal, HitlWaitMarker wait) {
        if (proposal == null || wait == null || !wait.structurallyValid()
                || (proposal.actionType() != null && proposal.actionType() != wait.actionType())) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST",
                    "HITL wait 或业务 Proposal 无效。", null, null);
        }
    }

    private PendingAction resolveRouting(String actionId, String presentedToken,
                                         VerifiedIdentity identity) {
        if (identity == null || identity.userId() == null || identity.userId().isBlank()) {
            throw new ActionException(HttpStatus.FORBIDDEN, "IDENTITY_REQUIRED",
                    "当前身份不可用。", null, null);
        }
        actionService.authorizeForAction(presentedToken, identity.asDemoIdentity());
        PendingAction action = actions.find(actionId).orElseThrow(() -> new ActionException(
                HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND", "未找到申请草稿。", null, null));
        if (action.ownerUserId() != null
                && !action.ownerUserId().equals(identity.userId())) {
            throw new ActionException(HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND",
                    "未找到申请草稿。", null, null);
        }
        return action;
    }

    private String guardKey(PendingAction action, VerifiedIdentity identity) {
        if (action.ownerUserId() != null && action.conversationId() != null) {
            // owner was checked against the current VerifiedIdentity above.
            return threadIdService.generate(identity.userId(), action.conversationId());
        }
        // Legacy rows have no immutable conversation correlation.  They still
        // use this singleton guard, but cannot collide with a Chat thread.
        return "legacy-action:" + action.actionId();
    }

    private void acquireOrBusy(String guardKey) {
        if (!threadGuard.tryAcquire(guardKey)) {
            throw new ActionException(HttpStatus.TOO_MANY_REQUESTS, "ACTION_THREAD_BUSY",
                    "当前会话正在处理中，请稍后重试。", null, null);
        }
    }

    private void reconcileAfterCommittedAction(String actionId, PendingAction routing,
                                               ActionExecutionResponse response,
                                               VerifiedIdentity identity,
                                               String presentedToken,
                                               String traceId,
                                               String guardKey) {
        PendingAction action = actions.find(actionId).orElse(routing);
        if (response.status() == ActionStatus.SUCCEEDED) {
            if (action.actionType() == com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM) {
                reconcileConfirmedExpense(action, response, identity, presentedToken, traceId, guardKey);
                return;
            }
            reconcileTerminal(actionId, action, identity, presentedToken, traceId,
                    HitlResumePayload.HitlDecision.CONFIRMED, ActionStatus.SUCCEEDED,
                    response.message(), response.requestId());
        } else if (response.status() == ActionStatus.CANCELLED) {
            reconcileTerminal(actionId, action, identity, presentedToken, traceId,
                    HitlResumePayload.HitlDecision.CANCELLED, ActionStatus.CANCELLED,
                    response.message(), null);
        }
    }

    private void reconcileConfirmedExpense(PendingAction action, ActionExecutionResponse response,
                                           VerifiedIdentity identity, String presentedToken,
                                           String traceId, String guardKey) {
        if (action.hitlWaitId() == null || action.agentExecutionId() == null
                || action.ownerUserId() == null || action.conversationId() == null) {
            return;
        }
        HitlResumePayload payload = new HitlResumePayload(1, action.hitlWaitId(),
                action.agentExecutionId(), HitlResumePayload.HitlDecision.CONFIRMED,
                action.actionId(), action.actionType(), ActionStatus.SUCCEEDED,
                response.requestId(), canonicalMessage(action, ActionStatus.SUCCEEDED, response.message()));
        try {
            PythonAgentResponse pythonResponse = postResume(action.ownerUserId(), identity.employeeId(),
                    action.conversationId(), presentedToken, traceId, payload);
            // The HITL resume has returned and the graph is now durably waiting
            // for OA.  Hand the same runtime thread boundary to the external
            // resume coordinator before it performs its own guard acquisition.
            threadGuard.release(guardKey);
            externalApprovalCoordinator.registerExternalWaitAndDispatch(action, response,
                    pythonResponse.externalWait(), traceId);
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_CONTINUATION_PENDING actionIdPrefix={} errorType={}", traceId,
                    BusinessActionService.auditRef(action.actionId()), exception.getClass().getSimpleName());
        }
    }

    private void reconcileTerminal(String actionId, PendingAction routing,
                                   VerifiedIdentity identity, String presentedToken,
                                   String traceId, HitlResumePayload.HitlDecision decision,
                                   ActionStatus status, String message, String requestId) {
        PendingAction action = actions.find(actionId).orElse(routing);
        reconcileTerminal(action, identity, presentedToken, traceId,
                decision, status, message, requestId);
    }

    private void reconcileTerminal(PendingAction action, VerifiedIdentity identity,
                                   String presentedToken, String traceId,
                                   HitlResumePayload.HitlDecision decision,
                                   ActionStatus status, String message, String requestId) {
        if (action == null || action.hitlWaitId() == null
                || action.agentExecutionId() == null
                || action.ownerUserId() == null || action.conversationId() == null) {
            return;
        }
        HitlResumePayload payload = new HitlResumePayload(
                1, action.hitlWaitId(), action.agentExecutionId(), decision,
                action.actionId(), action.actionType(), status, requestId,
                canonicalMessage(action, status, message));
        tryResume(action, identity, presentedToken, traceId, payload);
    }

    private void tryResumeRejected(HitlWaitMarker wait, VerifiedIdentity identity,
                                   String conversationId, String presentedToken,
                                   String traceId) {
        if (identity == null || identity.userId() == null || identity.employeeId() == null
                || conversationId == null || conversationId.isBlank()) {
            return;
        }
        HitlResumePayload payload = new HitlResumePayload(
                1, wait.waitId(), wait.executionId(), HitlResumePayload.HitlDecision.REJECTED,
                null, wait.actionType(), ActionStatus.FAILED, null, REJECTED_MESSAGE);
        try {
            postResume(identity.userId(), identity.employeeId(), conversationId,
                    presentedToken, traceId, payload);
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_REJECTION_CONTINUATION_PENDING waitIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(wait.waitId()),
                    exception.getClass().getSimpleName());
        }
    }

    private void tryResume(PendingAction action, VerifiedIdentity identity,
                           String presentedToken, String traceId) {
        HitlResumePayload.HitlDecision decision = switch (action.status()) {
            case SUCCEEDED -> HitlResumePayload.HitlDecision.CONFIRMED;
            case CANCELLED -> HitlResumePayload.HitlDecision.CANCELLED;
            case EXPIRED -> HitlResumePayload.HitlDecision.EXPIRED;
            case FAILED -> HitlResumePayload.HitlDecision.REJECTED;
            default -> null;
        };
        if (decision == null) {
            return;
        }
        ActionStatus status = action.status();
        tryResume(action, identity, presentedToken, traceId,
                new HitlResumePayload(1, action.hitlWaitId(), action.agentExecutionId(),
                        decision, action.actionId(), action.actionType(), status,
                        action.requestId(), canonicalMessage(action, status, null)));
    }

    private void tryResume(PendingAction action, VerifiedIdentity identity,
                           String presentedToken, String traceId,
                           HitlResumePayload payload) {
        try {
            postResume(action.ownerUserId(), identity.employeeId(), action.conversationId(),
                    presentedToken, traceId, payload);
        } catch (RuntimeException exception) {
            log.warn("[{}] HITL_CONTINUATION_PENDING actionIdPrefix={} errorType={}",
                    traceId, BusinessActionService.auditRef(action.actionId()),
                    exception.getClass().getSimpleName());
        }
    }

    private PythonAgentResponse postResume(String ownerUserId, String employeeId, String conversationId,
                                           String presentedToken, String traceId,
                                           HitlResumePayload payload) {
        String runtimeThreadId = threadIdService.generate(ownerUserId, conversationId);
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Agent-Thread-Id", runtimeThreadId);
        headers.set("X-Employee-Id", employeeId);
        headers.set("X-Allow-Eval", Boolean.toString(adminAccessService.isAdmin(presentedToken)));
        headers.set("X-Allow-Business-Actions",
                Boolean.toString(actionService.isAllowed(presentedToken)));
        LocalDate businessDate = actionService.businessDate();
        headers.set("X-Business-Date", businessDate.toString());
        headers.set("X-Conversation-Id", conversationId);
        return pythonAgentGateway.post("/agent/langgraph/hitl/resume", payload, headers,
                PythonAgentResponse.class, traceId);
    }

    private static String canonicalMessage(PendingAction action, ActionStatus status,
                                           String fallback) {
        return switch (status) {
            case SUCCEEDED -> boundedMessage(firstNonBlank(action.executionMessage(), fallback));
            case CANCELLED -> boundedMessage(firstNonBlank(action.executionMessage(), CANCELLED_MESSAGE));
            case EXPIRED -> EXPIRED_MESSAGE;
            case FAILED -> REJECTED_MESSAGE;
            default -> boundedMessage(fallback);
        };
    }

    private static String firstNonBlank(String preferred, String fallback) {
        return preferred == null || preferred.isBlank() ? fallback : preferred;
    }

    private static boolean isTerminal(ActionStatus status) {
        return status == ActionStatus.SUCCEEDED || status == ActionStatus.CANCELLED
                || status == ActionStatus.EXPIRED || status == ActionStatus.FAILED;
    }

    /**
     * Only explicit, deterministic proposal validation failures may close the
     * durable wait.  New business error codes must be reviewed and added here
     * deliberately; HTTP status alone is never sufficient classification.
     */
    private static boolean isDeterministicRegistrationRejection(ActionException exception) {
        if (exception == null || exception.errorCode() == null) {
            return false;
        }
        return switch (exception.errorCode()) {
            case "BUSINESS_RULE_VIOLATION",
                    "EXPENSE_ITEMS_REQUIRED",
                    "EXPENSE_AMOUNT_INVALID",
                    "EXPENSE_INVOICES_REQUIRED" -> true;
            default -> false;
        };
    }

    private static String boundedMessage(String message) {
        String value = message == null || message.isBlank() ? REJECTED_MESSAGE : message;
        return value.length() <= 255 ? value : value.substring(0, 255);
    }
}
