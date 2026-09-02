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
import com.fantuan.copilot.auth.DemoAuthPolicy;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.springframework.beans.factory.annotation.Autowired;
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
    private final TaskRuntimeService taskRuntimeService;
    private final SecureRandom secureRandom = new SecureRandom();

    @Autowired
    public BusinessActionService(BusinessActionProperties properties,
                                 AdminAccessService adminAccessService,
                                 PendingActionRepository actions,
                                 BusinessActionHandlerRegistry handlerRegistry,
                                 ActionNonceService nonceService,
                                 AiTaskMemoryService memoryService,
                                 AdminLogBuffer adminLogBuffer,
                                 Clock clock,
                                 TaskRuntimeService taskRuntimeService) {
        this.properties = properties;
        this.adminAccessService = adminAccessService;
        this.actions = actions;
        this.handlerRegistry = handlerRegistry;
        this.nonceService = nonceService;
        this.memoryService = memoryService;
        this.auditLogger = new BusinessActionAuditLogger(adminLogBuffer);
        this.clock = clock;
        this.taskRuntimeService = taskRuntimeService;
    }

    public LocalDate businessDate() { return LocalDate.now(clock); }

    public boolean isAllowed(String presentedToken) {
        return properties.isEnabled()
                && (!properties.isRequireAdmin() || adminAccessService.isAdmin(presentedToken));
    }

    /**
     * 已认证 Agent 请求的可信 capability 边界。
     * 即使全局动作开关和 Admin Token gate 都启用，公开 Demo 仍有意保持只读。
     */
    public boolean isAllowed(String presentedToken, VerifiedIdentity identity) {
        return isAllowed(presentedToken) && DemoAuthPolicy.mayUseBusinessActions(identity);
    }

    /**
     * legacy single actions 和 Task Runtime 共用的准入 guard。
     * 调用方只能提供可信的 Java owner 和已解析的 conversation。
     */
    @Transactional(readOnly = true)
    public boolean hasBlockingAction(String ownerUserId, String conversationId) {
        if (ownerUserId == null || ownerUserId.isBlank()
                || conversationId == null || conversationId.isBlank()) {
            return false;
        }
        return actions.hasActiveByOwnerAndConversation(ownerUserId, conversationId);
    }

    /**
     * 创建待确认动作。
     *
     * V2 §十六：Controller 不做 subtype 分发；本方法按 proposal.actionType()
     * 经 handlerRegistry → handler 调度业务校验与准备数据。
     *
     * conversationId 来自 Java 侧服务端解析的会话 ID（与 Memory 复合 key 对齐）；
     * ownerUserId 取自 trusted VerifiedIdentity.userId()。二者用于动作终态时收口
     * ACTIVE Memory，允许为 null（历史数据 / 无 Memory 关联）。
     */
    @Transactional
    public PendingActionView createPending(BusinessActionProposal proposal,
                                           String originTraceId,
                                           String presentedToken,
                                           VerifiedIdentity identity,
                                           String conversationId) {
        return createPendingInternal(proposal, originTraceId, presentedToken, identity,
                conversationId, null, null);
    }

    /**
     * P3-4 注册路径。wait correlation 不可变，并在与新 action 创建相同的全局
     * action control row 下检查。
     */
    @Transactional
    public PendingActionView createHitlPending(BusinessActionProposal proposal,
                                                String originTraceId,
                                                String presentedToken,
                                                VerifiedIdentity identity,
                                                String conversationId,
                                                String agentExecutionId,
                                                String hitlWaitId) {
        return createPendingInternal(proposal, originTraceId, presentedToken, identity,
                conversationId, agentExecutionId, hitlWaitId);
    }

    /** 在 coordinator 路由前保留 service 的授权顺序。 */
    void authorizeForAction(String presentedToken, VerifiedIdentity identity) {
        requireEnabledAndAdmin(presentedToken, identity);
        requireIdentity(identity);
    }

    /**
     * 当持久化 HITL Proposal 在创建 PendingAction 前被确定性拒绝时，关闭由 Java
     * 负责的 task memory。本方法有意使用 package-private；Python 永远不控制该
     * 生命周期转换。
     */
    void abandonMemoryAfterHitlRejection(VerifiedIdentity identity, String conversationId) {
        if (identity == null || identity.userId() == null || conversationId == null) {
            return;
        }
        memoryService.abandon(identity.userId(), conversationId);
    }

    /**
     * 在向 Python 发送新 Chat 前收口由 Java 负责的过期边界。数据库记录只在该
     * 短事务中锁定；Python continuation 有意由 coordinator 在本方法提交后执行。
     *
     * 未过期的 pending action 返回 empty，以保持现有 WAITING_USER 行为不变。
     * 已终态的 HITL action 仅用于支持上一次投递失败后的确定性 resume 重试。
     */
    @Transactional
    public Optional<PendingAction> reconcileExpiredForChat(String ownerUserId,
                                                            String conversationId,
                                                            String traceId) {
        if (ownerUserId == null || ownerUserId.isBlank()
                || conversationId == null || conversationId.isBlank()) {
            return Optional.empty();
        }
        Instant now = clock.instant();
        Optional<PendingAction> pending = actions
                .findPendingConfirmationByOwnerAndConversationForUpdate(
                        ownerUserId, conversationId);
        if (pending.isPresent()) {
            PendingAction action = pending.get();
            if (action.expiresAt() == null || now.isBefore(action.expiresAt())) {
                return Optional.empty();
            }
            actions.markExpired(action.actionId(), now);
            audit(traceId == null ? action.originTraceId() : traceId, action,
                    ActionStatus.PENDING_CONFIRMATION, ActionStatus.EXPIRED,
                    "ACTION_EXPIRED", null, now);
            synchronizeTaskRuntime(action, TaskExecutionStatus.EXPIRED);
            closeMemory(action, TaskStatus.ABANDONED);
            return actions.find(action.actionId());
        }
         // 如果上一次请求已提交 EXPIRED 但 Python 不可用，在允许新请求检查
         // checkpoint 前，先重试同一个权威 continuation。
        Optional<PendingAction> expired = actions.findLatestExpiredHitlByOwnerAndConversation(
                ownerUserId, conversationId);
        expired.ifPresent(action -> synchronizeTaskRuntime(action, TaskExecutionStatus.EXPIRED));
        return expired;
    }

    private PendingActionView createPendingInternal(BusinessActionProposal proposal,
                                                     String originTraceId,
                                                     String presentedToken,
                                                     VerifiedIdentity identity,
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
            requireEnabledAndAdmin(presentedToken, identity);
            requireIdentity(identity);
            throw new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "BUSINESS_RULE_VIOLATION", "action_type 缺失，请检查申请参数。", null, null);
        }
        BusinessActionHandler handler = handlerRegistry.handlerFor(actionType)
                .orElseThrow(() -> new ActionException(HttpStatus.BAD_REQUEST,
                        "INVALID_REQUEST",
                        "暂不支持的 action subtype: " + actionType, null, null));
        if (hitlWaitId != null) {
                    // 终态 HITL 记录只需要可信身份/关联信息来执行 checkpoint
                    // reconciliation。每个新记录或仍可操作的记录都会在下方检查
                    // 业务 capability。
            requireIdentity(identity);
            actions.lockControl();
            Optional<PendingAction> existingResult = actions.findByHitlWaitIdForUpdate(hitlWaitId);
            PendingAction existing = existingResult == null ? null : existingResult.orElse(null);
            if (existing != null) {
                verifyHitlCorrelation(existing, proposal.actionType(), identity,
                        conversationId, agentExecutionId, hitlWaitId);
                if (isTerminal(existing.status())) {
                    // Java 终态结果必须能够在 capability 撤销后完成审批 checkpoint。
                    return handler.buildSummary(existing, null);
                }
                requireEnabledAndAdmin(presentedToken, identity);
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
        requireEnabledAndAdmin(presentedToken, identity);
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
            synchronizeTaskRuntime(action, TaskExecutionStatus.EXPIRED);
            closeMemory(action, TaskStatus.ABANDONED);
        });
        if (actions.countActive() >= properties.getMaxPending()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "ACTION_CAPACITY_EXCEEDED", "待确认申请数量已达到上限。", null, null);
        }
        // 同一会话至多一个活动动作：Task Runtime 不能绕过这个 invariant；
        // 正常 successor 创建时前序 action 已经 terminal。conversationId 为 null
        //（无 Memory 关联的历史路径）时不做限制。
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
                                           String traceId, VerifiedIdentity identity) {
        requireEnabledAndAdmin(presentedToken, identity);
        requireIdentity(identity);
        PendingAction action = findForUpdate(actionId);
        verifyOwner(action, identity);
        UUID key = parseIdempotencyKey(idempotencyKey);
        Instant now = clock.instant();
        expireIfNeeded(action, now);
        verifyNonce(action, confirmationNonce);

        if (action.status() == ActionStatus.SUCCEEDED) {
            if (isTaskRuntimeAction(action)) {
                // Task Runtime replay 只验证当前 task 没有倒退；不能重新
                // 关闭 conversation Memory，因为它可能已经属于 successor。
                if (!taskRuntimeService.synchronizeReplayStatus(
                        action.actionId(), taskStatusAfterConfirmation(action))) {
                    throw new IllegalStateException("TaskExecution replay status conflict");
                }
            } else {
                // Legacy 幂等重放：保持原有 Memory 收口语义。
                synchronizeTaskRuntime(action, taskStatusAfterConfirmation(action));
                closeMemory(action, TaskStatus.COMPLETED);
            }
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
        // TaskExecution 与 PendingAction/业务表/Memory 必须在本事务内完成。
        // Python resume 和后续任务启动由 coordinator 在 commit 后执行。
        synchronizeTaskRuntime(action, taskStatusAfterConfirmation(action));
        // 动作最终成功：Memory 在同一事务内收口为 COMPLETED，不再注入后续 Planner
        closeMemory(action, TaskStatus.COMPLETED);
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.SUCCEEDED, requestId, successMessage, false, now,
                action.originTraceId(), traceId);
    }

    /**
     * 外部检查在事务外完成后，收口确定性的 stale 报销确认。短事务会重新检查
     * 所有本地权威信息，且永远不会覆盖另一个终态。
     */
    @Transactional(noRollbackFor = ActionStaleException.class)
    public void failStaleConfirmation(String actionId, String confirmationNonce,
                                      String presentedToken, String traceId,
                                      VerifiedIdentity identity, String failureCode) {
        requireEnabledAndAdmin(presentedToken, identity);
        requireIdentity(identity);
        PendingAction action = findForUpdate(actionId);
        verifyOwner(action, identity);
        if (!handlerRegistry.acceptsStaleFailureCode(action.actionType(), failureCode)) {
            throw new IllegalArgumentException("Unsupported stale failure code");
        }
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
            synchronizeTaskRuntime(action, TaskExecutionStatus.FAILED);
            closeMemory(action, TaskStatus.ABANDONED);
        throw new ActionStaleException(action.actionId());
    }

    @Transactional(noRollbackFor = ActionExpiredAfterUpdateException.class)
    public ActionExecutionResponse cancel(String actionId, String confirmationNonce,
                                          String presentedToken, String traceId,
                                          VerifiedIdentity identity) {
        requireEnabledAndAdmin(presentedToken, identity);
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
        synchronizeTaskRuntime(action, TaskExecutionStatus.CANCELLED);
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
            synchronizeTaskRuntime(action, TaskExecutionStatus.EXPIRED);
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

    private TaskExecutionStatus taskStatusAfterConfirmation(PendingAction action) {
        return handlerRegistry.handlerFor(action.actionType())
                .map(BusinessActionHandler::statusAfterConfirmation)
                .orElseThrow(() -> new IllegalStateException(
                        "No BusinessActionHandler for action type: " + action.actionType()));
    }

    private void synchronizeTaskRuntime(PendingAction action, TaskExecutionStatus target) {
        if (action == null) {
            return;
        }
        if (!taskRuntimeService.synchronizeBusinessStatus(action.actionId(), target)) {
            throw new IllegalStateException("TaskExecution business status transition conflict");
        }
    }

    private boolean isTaskRuntimeAction(PendingAction action) {
        return action != null
                && taskRuntimeService.findByActionId(action.actionId()).isPresent();
    }

    private void requireEnabledAndAdmin(String presentedToken, VerifiedIdentity identity) {
        if (!properties.isEnabled()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "BUSINESS_ACTIONS_DISABLED", "业务动作功能当前未启用。", null, null);
        }
        if (properties.isRequireAdmin() && !adminAccessService.isAdmin(presentedToken)) {
            throw new ActionException(HttpStatus.FORBIDDEN,
                    "ADMIN_REQUIRED", "需要管理员权限。", null, null);
        }
        if (DemoAuthPolicy.isPublicDemo(identity)) {
            throw new ActionException(HttpStatus.FORBIDDEN,
                    "BUSINESS_ACTIONS_NOT_ALLOWED", "当前 Demo 账号仅支持只读能力。", null, null);
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

    private void verifyHitlCorrelation(PendingAction existing,
                                       BusinessActionType actionType,
                                       VerifiedIdentity identity,
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

    private void requireIdentity(VerifiedIdentity identity) {
        if (identity == null || identity.employeeId() == null || identity.employeeId().isBlank()) {
            throw new ActionException(HttpStatus.FORBIDDEN, "EMPLOYEE_ID_REQUIRED",
                    "当前身份不是员工身份。", null, null);
        }
    }

    private void verifyOwner(PendingAction action, VerifiedIdentity identity) {
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
