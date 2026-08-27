package com.fantuan.copilot.service.action;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Base64;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * 业务动作通用生命周期 Service（V2 §十七）。
 *
 * 只负责：feature enabled / admin / identity / capacity / conversation
 * 活动动作约束 / PendingAction 状态机 / nonce / TTL / idempotency / audit /
 * Memory 终态收口。
 *
 * 业务专属逻辑（校验 / 准备数据 / 执行副作用 / summary）由
 * BusinessActionHandlerRegistry 注入的 handler 完成：
 *   proposal.actionType() → handlerRegistry.handlerFor(type)
 *   → handler.planPending / revalidateBeforeExecute / execute / buildSummary
 *
 * **禁止** instanceof / enum switch 分发（V2 §十七）。
 */
@Service
public class BusinessActionService {
    private static final String CANCELLED_MESSAGE = "申请草稿已取消。";

    private final BusinessActionProperties properties;
    private final AdminAccessService adminAccessService;
    private final PendingActionRepository actions;
    private final BusinessActionHandlerRegistry handlerRegistry;
    private final ActionNonceService nonceService;
    private final AiTaskMemoryService memoryService;
    private final BusinessActionAuditLogger auditLogger;
    private final Clock clock;
    private final SecureRandom secureRandom = new SecureRandom();

    public BusinessActionService(BusinessActionProperties properties,
                                 AdminAccessService adminAccessService,
                                 PendingActionRepository actions,
                                 BusinessActionHandlerRegistry handlerRegistry,
                                 ActionNonceService nonceService,
                                 AiTaskMemoryService memoryService,
                                 AdminLogBuffer adminLogBuffer,
                                 Clock clock) {
        this.properties = properties;
        this.adminAccessService = adminAccessService;
        this.actions = actions;
        this.handlerRegistry = handlerRegistry;
        this.nonceService = nonceService;
        this.memoryService = memoryService;
        this.auditLogger = new BusinessActionAuditLogger(adminLogBuffer);
        this.clock = clock;
    }

    public LocalDate businessDate() { return LocalDate.now(clock); }

    public boolean isAllowed(String presentedToken) {
        return properties.isEnabled()
                && (!properties.isRequireAdmin() || adminAccessService.isAdmin(presentedToken));
    }

    /**
     * 创建待确认动作。
     *
     * V2 §十六：Controller 不做 subtype 分发；本方法按 proposal.actionType()
     * 经 handlerRegistry → handler 调度业务校验与准备数据。
     *
     * conversationId 来自 Java 侧服务端解析的会话 ID（与 Memory 复合 key 对齐）；
     * ownerUserId 取自 trusted DemoIdentity.userId()。二者用于动作终态时收口
     * ACTIVE Memory，允许为 null（历史数据 / 无 Memory 关联）。
     */
    @Transactional
    public PendingActionView createPending(BusinessActionProposal proposal,
                                           String originTraceId,
                                           String presentedToken,
                                           DemoIdentity identity,
                                           String conversationId) {
        return createPendingInternal(proposal, originTraceId, presentedToken, identity,
                conversationId, null, null);
    }

    /**
     * P3-4 registration path.  The wait correlation is immutable and is
     * checked under the same global action control row as new action creation.
     */
    @Transactional
    public PendingActionView createHitlPending(BusinessActionProposal proposal,
                                                String originTraceId,
                                                String presentedToken,
                                                DemoIdentity identity,
                                                String conversationId,
                                                String agentExecutionId,
                                                String hitlWaitId) {
        return createPendingInternal(proposal, originTraceId, presentedToken, identity,
                conversationId, agentExecutionId, hitlWaitId);
    }

    /** Preserve the service's authorization ordering before coordinator routing. */
    void authorizeForAction(String presentedToken, DemoIdentity identity) {
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
    }

    /**
     * Close the Java-owned task memory when a durable HITL Proposal is
     * deterministically rejected before a PendingAction can be created.
     * This is intentionally package-private: Python never controls this
     * lifecycle transition.
     */
    void abandonMemoryAfterHitlRejection(DemoIdentity identity, String conversationId) {
        if (identity == null || identity.userId() == null || conversationId == null) {
            return;
        }
        memoryService.abandon(identity.userId(), conversationId);
    }

    private PendingActionView createPendingInternal(BusinessActionProposal proposal,
                                                     String originTraceId,
                                                     String presentedToken,
                                                     DemoIdentity identity,
                                                     String conversationId,
                                                     String agentExecutionId,
                                                     String hitlWaitId) {
        if (proposal == null) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST",
                    "缺少 action_proposal。", null, null);
        }
        if (hitlWaitId != null && (hitlWaitId.isBlank()
                || agentExecutionId == null || agentExecutionId.isBlank())) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST",
                    "HITL wait correlation 不完整。", null, null);
        }
        // 唯一业务分发点：actionType() → registry → handler（V2 §十七）。
        // action_type 缺失属于未信任 proposal 的规则违规（BUSINESS_RULE_VIOLATION）；
        // 未知非空类型属于协议层错误（INVALID_REQUEST）。
        BusinessActionType actionType = proposal.actionType();
        if (actionType == null) {
            requireEnabledAndAdmin(presentedToken);
            requireIdentity(identity);
            throw new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "BUSINESS_RULE_VIOLATION", "action_type 缺失，请检查申请参数。", null, null);
        }
        BusinessActionHandler handler = handlerRegistry.handlerFor(actionType)
                .orElseThrow(() -> new ActionException(HttpStatus.BAD_REQUEST,
                        "INVALID_REQUEST",
                        "暂不支持的 action subtype: " + actionType, null, null));
        if (hitlWaitId != null) {
            // Terminal HITL rows only need trusted identity/correlation for
            // checkpoint reconciliation.  Business capability is checked
            // below for every new or still-actionable row.
            requireIdentity(identity);
            actions.lockControl();
            Optional<PendingAction> existingResult = actions.findByHitlWaitIdForUpdate(hitlWaitId);
            PendingAction existing = existingResult == null ? null : existingResult.orElse(null);
            if (existing != null) {
                verifyHitlCorrelation(existing, proposal.actionType(), identity,
                        conversationId, agentExecutionId, hitlWaitId);
                if (isTerminal(existing.status())) {
                    // A Java terminal result must be able to finish the
                    // approval checkpoint after capability revocation.
                    return handler.buildSummary(existing, null);
                }
                requireEnabledAndAdmin(presentedToken);
                if (existing.status() == ActionStatus.PENDING_CONFIRMATION) {
                    ActionNonceService.Nonce nonce = nonceService.create();
                    actions.updateConfirmationNonceDigest(
                            existing.actionId(), nonce.digest());
                    return handler.buildSummary(existing, nonce.plaintext());
                }
                if (existing.status() == ActionStatus.PROCESSING) {
                    throw error(HttpStatus.CONFLICT, "ACTION_IN_PROGRESS",
                            "申请正在处理中。", existing);
                }
            }
        }
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
        if (hitlWaitId == null) {
            actions.lockControl();
        }
        Instant now = clock.instant();
        BusinessActionHandler.PendingPlan plan =
                handler.planPending(proposal, identity, businessDate(), now);
        List<PendingAction> expired = actions.findExpired(now);
        actions.expirePending(now);
        // 批量过期动作先收口 Memory（与 PendingAction 同一事务，避免泄漏 ACTIVE 记忆）
        expired.forEach(action -> {
            audit(action.originTraceId(), action, ActionStatus.PENDING_CONFIRMATION,
                    ActionStatus.EXPIRED, "ACTION_EXPIRED", null, now);
            closeMemory(action, TaskStatus.ABANDONED);
        });
        if (actions.countActive() >= properties.getMaxPending()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "ACTION_CAPACITY_EXCEEDED", "待确认申请数量已达到上限。", null, null);
        }
        // 同一会话至多一个活动动作：ai_task_memory 以 (user_id, conversation_id) 为唯一键，
        // 多活动动作会互相终结对方的任务记忆（任一终态都会收口整条会话 Memory）。
        // conversationId 为 null（无 Memory 关联的历史路径）时不做限制。
        if (conversationId != null && identity.userId() != null
                && actions.hasActiveByOwnerAndConversation(identity.userId(), conversationId)) {
            throw new ActionException(HttpStatus.CONFLICT, "ACTION_CONVERSATION_IN_PROGRESS",
                    "当前会话已有待确认的申请，请先确认或取消后再发起新申请。", null, null);
        }

        // handler 生成业务字段（AnnualLeave：startDate/days 等；Expense：由
        // Phase 6 ExpenseClaimActionHandler 解析 payloadJson 提供 null）。
        // 通用阶段只关心 actionType + nonce/status/TTL + handler 返回的
        // 业务字段（保留 V1 语义与 V6 CHECK）。
        ActionNonceService.Nonce nonce = nonceService.create();
        PendingAction action = PendingAction.pending(randomActionId(),
                proposal.actionType(), originTraceId,
                identity.userId(), conversationId,
                identity.employeeId(), identity.displayName(),
                plan.startDate(), plan.endDate(), plan.halfDay(), plan.reason(),
                plan.days(), plan.balanceBefore(), plan.balanceAfter(), nonce.digest(), now,
                now.plusSeconds(properties.getTtlSeconds()), plan.payloadJson(),
                agentExecutionId, hitlWaitId);
        actions.saveNew(action);
        if (actions.size() > properties.getMaxPending() + properties.getMaxCompleted()) {
            actions.maintainBounds(properties.getMaxCompleted());
        }
        audit(originTraceId, action, null, ActionStatus.PENDING_CONFIRMATION,
                "ACTION_CREATED", null, null);
        return handler.buildSummary(action, nonce.plaintext());
    }

    @Transactional(noRollbackFor = {
            ActionStaleException.class,
            ActionExpiredAfterUpdateException.class
    })
    public ActionExecutionResponse confirm(String actionId, String confirmationNonce,
                                           String idempotencyKey, String presentedToken,
                                           String traceId, DemoIdentity identity) {
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
        PendingAction action = findForUpdate(actionId);
        verifyOwner(action, identity);
        UUID key = parseIdempotencyKey(idempotencyKey);
        Instant now = clock.instant();
        expireIfNeeded(action, now);
        verifyNonce(action, confirmationNonce);

        if (action.status() == ActionStatus.SUCCEEDED) {
            // 幂等重放：Memory 收口同样幂等（COMPLETED → COMPLETE 白名单内）
            closeMemory(action, TaskStatus.COMPLETED);
            return succeededResponse(action, traceId, true);
        }
        if (action.status() == ActionStatus.PROCESSING) {
            throw error(HttpStatus.CONFLICT, "ACTION_IN_PROGRESS", "申请正在处理中。", action);
        }
        if (action.status() != ActionStatus.PENDING_CONFIRMATION) {
            throw error(HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT", "当前状态不能确认。", action);
        }

        BusinessActionHandler handler = handlerRegistry.handlerFor(action.actionType())
                .orElseThrow(() -> new ActionException(HttpStatus.BAD_REQUEST,
                        "INVALID_REQUEST",
                        "暂不支持的 action subtype: " + action.actionType(), null, null));

        actions.markProcessing(action.actionId(), key);
        String staleCode = handler.revalidateBeforeExecute(action);
        if (staleCode != null) {
            actions.markFailed(action.actionId(), staleCode, now);
            audit(traceId, action, ActionStatus.PROCESSING, ActionStatus.FAILED,
                    staleCode, null, now);
            closeMemory(action, TaskStatus.ABANDONED);
            throw new ActionStaleException(action.actionId());
        }

        BusinessActionHandler.ExecutionExecutionResult execution =
                handler.execute(action, now);
        String requestId = execution.requestId();
        String successMessage = execution.message();
        actions.markSucceeded(action.actionId(), requestId, successMessage, now);
        audit(traceId, action, ActionStatus.PROCESSING, ActionStatus.SUCCEEDED,
                "ACTION_SUCCEEDED", requestId, now);
        // 动作最终成功：Memory 在同一事务内收口为 COMPLETED，不再注入后续 Planner
        closeMemory(action, TaskStatus.COMPLETED);
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.SUCCEEDED, requestId, successMessage, false, now,
                action.originTraceId(), traceId);
    }

    /**
     * Finalize a deterministic stale expense confirmation after the external
     * check completed outside a transaction.  The short transaction rechecks
     * all local authority and never overwrites another terminal state.
     */
    @Transactional(noRollbackFor = ActionStaleException.class)
    public void failStaleConfirmation(String actionId, String confirmationNonce,
                                      String presentedToken, String traceId,
                                      DemoIdentity identity, String failureCode) {
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
        if (!isStaleFailureCode(failureCode)) {
            throw new IllegalArgumentException("Unsupported stale failure code");
        }
        PendingAction action = findForUpdate(actionId);
        verifyOwner(action, identity);
        Instant now = clock.instant();
        expireIfNeeded(action, now);
        verifyNonce(action, confirmationNonce);
        if (action.status() != ActionStatus.PENDING_CONFIRMATION) {
            throw error(HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT",
                    "当前状态不能拒绝确认。", action);
        }
        actions.markFailed(action.actionId(), failureCode, now);
        audit(traceId, action, ActionStatus.PENDING_CONFIRMATION, ActionStatus.FAILED,
                failureCode, null, now);
        closeMemory(action, TaskStatus.ABANDONED);
        throw new ActionStaleException(action.actionId());
    }

    @Transactional(noRollbackFor = ActionExpiredAfterUpdateException.class)
    public ActionExecutionResponse cancel(String actionId, String confirmationNonce,
                                          String presentedToken, String traceId,
                                          DemoIdentity identity) {
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
        PendingAction action = findForUpdate(actionId);
        verifyOwner(action, identity);
        Instant now = clock.instant();
        expireIfNeeded(action, now);
        verifyNonce(action, confirmationNonce);
        if (action.status() == ActionStatus.CANCELLED) {
            closeMemory(action, TaskStatus.ABANDONED);
            return cancelledResponse(action, traceId, true);
        }
        if (action.status() != ActionStatus.PENDING_CONFIRMATION) {
            throw error(HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT", "当前状态不能取消。", action);
        }
        actions.markCancelled(action.actionId(), CANCELLED_MESSAGE, now);
        audit(traceId, action, ActionStatus.PENDING_CONFIRMATION, ActionStatus.CANCELLED,
                "ACTION_CANCELLED", null, now);
        closeMemory(action, TaskStatus.ABANDONED);
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.CANCELLED, null, CANCELLED_MESSAGE, false, now,
                action.originTraceId(), traceId);
    }

    private ActionExecutionResponse succeededResponse(PendingAction action,
                                                       String traceId, boolean replayed) {
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.SUCCEEDED, action.requestId(), action.executionMessage(), replayed,
                action.completedAt(), action.originTraceId(), traceId);
    }

    private ActionExecutionResponse cancelledResponse(PendingAction action,
                                                       String traceId, boolean replayed) {
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.CANCELLED, null, action.executionMessage(), replayed,
                action.completedAt(), action.originTraceId(), traceId);
    }

    private void expireIfNeeded(PendingAction action, Instant now) {
        if (action.status() == ActionStatus.PENDING_CONFIRMATION && !now.isBefore(action.expiresAt())) {
            actions.markExpired(action.actionId(), now);
            audit(action.originTraceId(), action, ActionStatus.PENDING_CONFIRMATION,
                    ActionStatus.EXPIRED, "ACTION_EXPIRED", null, now);
            closeMemory(action, TaskStatus.ABANDONED);
            throw new ActionExpiredAfterUpdateException(action.actionId());
        }
        if (action.status() == ActionStatus.EXPIRED) {
            throw error(HttpStatus.GONE, "ACTION_EXPIRED", "该申请草稿已过期，请重新生成。", action);
        }
    }

    /**
     * Memory 生命周期收口：PendingAction 进入终态时同步终结 ACTIVE 任务记忆，
     * 保证"动作已确认 / 取消 / 过期后 Memory 不再注入 Planner"。
     * ownerUserId / conversationId 为 null（历史数据）时跳过；
     * 记录不存在或已是另一终态时由 AiTaskMemoryService 幂等返回 false。
     * 与 PendingAction 状态变更在同一事务内执行。
     */
    private void closeMemory(PendingAction action, TaskStatus target) {
        if (action.ownerUserId() == null || action.conversationId() == null) {
            return;
        }
        if (target == TaskStatus.COMPLETED) {
            memoryService.complete(action.ownerUserId(), action.conversationId());
        } else {
            memoryService.abandon(action.ownerUserId(), action.conversationId());
        }
    }

    private void requireEnabledAndAdmin(String presentedToken) {
        if (!properties.isEnabled()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "BUSINESS_ACTIONS_DISABLED", "业务动作功能当前未启用。", null, null);
        }
        if (properties.isRequireAdmin() && !adminAccessService.isAdmin(presentedToken)) {
            throw new ActionException(HttpStatus.FORBIDDEN,
                    "ADMIN_REQUIRED", "需要管理员权限。", null, null);
        }
    }

    private PendingAction findForUpdate(String actionId) {
        return actions.findForUpdate(actionId).orElseThrow(() -> new ActionException(
                HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND", "未找到申请草稿。", null, null));
    }

    public Optional<PendingAction> findByHitlWaitId(String hitlWaitId) {
        if (hitlWaitId == null || hitlWaitId.isBlank()) {
            return Optional.empty();
        }
        return actions.findByHitlWaitId(hitlWaitId);
    }

    private static boolean isTerminal(ActionStatus status) {
        return status == ActionStatus.SUCCEEDED || status == ActionStatus.CANCELLED
                || status == ActionStatus.EXPIRED || status == ActionStatus.FAILED;
    }

    private static boolean isStaleFailureCode(String failureCode) {
        return switch (failureCode) {
            case "EXPENSE_TRIP_STALE", "EXPENSE_INVOICE_STALE", "EXPENSE_AMOUNT_STALE" -> true;
            default -> false;
        };
    }

    private void verifyHitlCorrelation(PendingAction existing,
                                       BusinessActionType actionType,
                                       DemoIdentity identity,
                                       String conversationId,
                                       String agentExecutionId,
                                       String hitlWaitId) {
        boolean same = Objects.equals(existing.hitlWaitId(), hitlWaitId)
                && Objects.equals(existing.agentExecutionId(), agentExecutionId)
                && Objects.equals(existing.actionType(), actionType)
                && Objects.equals(existing.ownerUserId(), identity.userId())
                && Objects.equals(existing.conversationId(), conversationId)
                && Objects.equals(existing.employeeId(), identity.employeeId());
        if (!same) {
            throw new ActionException(HttpStatus.CONFLICT, "ACTION_HITL_WAIT_CONFLICT",
                    "HITL wait 归属不匹配，已拒绝复用。", existing.actionId(), existing.status());
        }
    }

    private void requireIdentity(DemoIdentity identity) {
        if (identity == null || identity.employeeId() == null || identity.employeeId().isBlank()) {
            throw new ActionException(HttpStatus.FORBIDDEN, "EMPLOYEE_ID_REQUIRED",
                    "当前身份不是员工身份。", null, null);
        }
    }

    private void verifyOwner(PendingAction action, DemoIdentity identity) {
        if (!action.employeeId().equals(identity.employeeId())) {
            throw new ActionException(HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND",
                    "未找到申请草稿。", null, null);
        }
    }

    private void verifyNonce(PendingAction action, String plaintext) {
        if (!nonceService.matches(plaintext, action.confirmationNonceDigest())) {
            throw error(HttpStatus.FORBIDDEN, "INVALID_CONFIRMATION_NONCE", "确认凭据无效。", action);
        }
    }

    private UUID parseIdempotencyKey(String value) {
        try {
            return UUID.fromString(value);
        } catch (RuntimeException exception) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key必须是UUID。", null, null);
        }
    }

    private String randomActionId() {
        byte[] bytes = new byte[18];
        secureRandom.nextBytes(bytes);
        return "act_" + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private ActionException rule(String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", message, null, null);
    }

    private ActionException error(HttpStatus status, String code, String message,
                                  PendingAction action) {
        return new ActionException(status, code, message, action.actionId(), action.status());
    }

    void audit(String traceId, PendingAction action, ActionStatus from,
               ActionStatus to, String resultCode, String requestId, Instant completedAt) {
        auditLogger.audit(traceId, action, from, to, resultCode, requestId, completedAt);
    }

    static String auditRef(String rawId) {
        return BusinessActionAuditLogger.auditRef(rawId);
    }

}
