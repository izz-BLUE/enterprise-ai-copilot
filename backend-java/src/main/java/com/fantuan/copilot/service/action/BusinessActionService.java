package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.AnnualLeaveSummary;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.leave.LeaveExecutionGateway;
import com.fantuan.copilot.gateway.leave.LeaveExecutionResult;
import com.fantuan.copilot.gateway.leave.LeaveSubmission;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

@Service
public class BusinessActionService {
    private static final Logger log = LoggerFactory.getLogger(BusinessActionService.class);
    private static final String SUCCESS_MESSAGE = "模拟年假申请已提交。";
    private static final String CANCELLED_MESSAGE = "申请草稿已取消。";

    private final BusinessActionProperties properties;
    private final AdminAccessService adminAccessService;
    private final PendingActionRepository actions;
    private final LeaveAccountRepository accounts;
    private final LeaveExecutionGateway leaveExecutionGateway;
    private final ActionNonceService nonceService;
    private final AiTaskMemoryService memoryService;
    private final Clock clock;
    private final SecureRandom secureRandom = new SecureRandom();

    public BusinessActionService(BusinessActionProperties properties,
                                 AdminAccessService adminAccessService,
                                 PendingActionRepository actions,
                                 LeaveAccountRepository accounts,
                                 LeaveExecutionGateway leaveExecutionGateway,
                                 ActionNonceService nonceService,
                                 AiTaskMemoryService memoryService,
                                 Clock clock) {
        this.properties = properties;
        this.adminAccessService = adminAccessService;
        this.actions = actions;
        this.accounts = accounts;
        this.leaveExecutionGateway = leaveExecutionGateway;
        this.nonceService = nonceService;
        this.memoryService = memoryService;
        this.clock = clock;
    }

    public LocalDate businessDate() { return LocalDate.now(clock); }

    public boolean isAllowed(String presentedToken) {
        return properties.isEnabled()
                && (!properties.isRequireAdmin() || adminAccessService.isAdmin(presentedToken));
    }

    public void requireAccess(String presentedToken) {
        requireEnabledAndAdmin(presentedToken);
    }

    /**
     * 创建待确认动作。conversationId 来自 Java 侧服务端解析的会话 ID（与 Memory
     * 复合 key 对齐）；ownerUserId 取自 trusted DemoIdentity.userId()。二者用于
     * 动作终态时收口 ACTIVE Memory，允许为 null（历史数据 / 无 Memory 关联）。
     */
    @Transactional
    public PendingActionView createPending(AnnualLeaveActionProposal proposal,
                                           String originTraceId,
                                           String presentedToken,
                                           DemoIdentity identity,
                                           String conversationId) {
        requireEnabledAndAdmin(presentedToken);
        requireIdentity(identity);
        ValidatedLeave validated = validate(proposal);
        Instant now = clock.instant();

        actions.lockControl();
        List<PendingAction> expired = actions.findExpired(now);
        actions.expirePending(now);
        // 批量过期动作先收口 Memory（与 PendingAction 同一事务，避免泄漏 ACTIVE 记忆）
        expired.forEach(action -> closeMemory(action, TaskStatus.ABANDONED));
        if (actions.countActive() >= properties.getMaxPending()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "ACTION_CAPACITY_EXCEEDED", "待确认申请数量已达到上限。", null, null);
        }

        BigDecimal balanceBefore = accounts.findBalanceForUpdate(identity.employeeId())
                .orElseThrow(() -> new IllegalStateException("Demo leave account unavailable"));
        if (leaveExecutionGateway.hasConflict(identity.employeeId(),
                proposal.startDate(), proposal.endDate())) {
            throw rule("日期范围与已提交的模拟申请冲突。");
        }
        if (balanceBefore.compareTo(validated.days()) < 0) {
            throw rule("模拟年假余额不足。");
        }

        BigDecimal balanceAfter = balanceBefore.subtract(validated.days());
        ActionNonceService.Nonce nonce = nonceService.create();
        PendingAction action = PendingAction.pending(randomActionId(),
                BusinessActionType.ANNUAL_LEAVE_REQUEST, originTraceId,
                identity.userId(), conversationId,
                identity.employeeId(), identity.displayName(),
                proposal.startDate(), proposal.endDate(), proposal.halfDay(), validated.reason(),
                validated.days(), balanceBefore, balanceAfter, nonce.digest(), now,
                now.plusSeconds(properties.getTtlSeconds()));
        actions.saveNew(action);
        actions.maintainBounds(properties.getMaxCompleted());
        audit(originTraceId, action, null, ActionStatus.PENDING_CONFIRMATION,
                "ACTION_CREATED", null, null);
        return pendingView(action, nonce.plaintext());
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

        actions.markProcessing(action.actionId(), key);
        BigDecimal currentBalance = accounts.findBalanceForUpdate(action.employeeId())
                .orElseThrow(() -> new IllegalStateException("Leave account unavailable"));
        if (currentBalance.compareTo(action.balanceBefore()) != 0
                || currentBalance.compareTo(action.days()) < 0
                || leaveExecutionGateway.hasConflict(action.employeeId(),
                action.startDate(), action.endDate())) {
            actions.markFailed(action.actionId(), "ACTION_STALE", now);
            audit(traceId, action, ActionStatus.PROCESSING, ActionStatus.FAILED,
                    "ACTION_STALE", null, now);
            closeMemory(action, TaskStatus.ABANDONED);
            throw new ActionStaleException(action.actionId());
        }

        LeaveExecutionResult execution = leaveExecutionGateway.submit(new LeaveSubmission(
                action.actionId(), action.employeeId(), action.startDate(), action.endDate(),
                action.halfDay(), action.days(), now));
        String requestId = execution.requestId();
        accounts.updateBalance(action.employeeId(), currentBalance.subtract(action.days()), now);
        actions.markSucceeded(action.actionId(), requestId, SUCCESS_MESSAGE, now);
        audit(traceId, action, ActionStatus.PROCESSING, ActionStatus.SUCCEEDED,
                "ACTION_SUCCEEDED", requestId, now);
        // 动作最终成功：Memory 在同一事务内收口为 COMPLETED，不再注入后续 Planner
        closeMemory(action, TaskStatus.COMPLETED);
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.SUCCEEDED, requestId, SUCCESS_MESSAGE, false, now,
                action.originTraceId(), traceId);
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

    private PendingActionView pendingView(PendingAction action, String plaintextNonce) {
        return new PendingActionView(action.actionId(), action.actionType(), action.status(),
                "提交模拟年假申请", new AnnualLeaveSummary(action.displayName(),
                action.startDate(), action.endDate(), action.halfDay(), action.days(),
                action.reason(), action.balanceBefore(), action.balanceAfter()),
                plaintextNonce, action.expiresAt(), true);
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

    private ValidatedLeave validate(AnnualLeaveActionProposal proposal) {
        if (proposal == null || proposal.actionType() != BusinessActionType.ANNUAL_LEAVE_REQUEST
                || proposal.startDate() == null || proposal.endDate() == null
                || proposal.halfDay() == null) {
            throw rule("年假申请参数不完整。");
        }
        LocalDate businessDate = businessDate();
        if (proposal.startDate().isBefore(businessDate)) {
            throw rule("开始日期不能早于当前业务日期。");
        }
        if (proposal.endDate().isBefore(proposal.startDate())) {
            throw rule("结束日期不能早于开始日期。");
        }
        long span = ChronoUnit.DAYS.between(proposal.startDate(), proposal.endDate()) + 1;
        if (span > 31) {
            throw rule("申请日期跨度不能超过31个日历日。");
        }
        String rawReason = proposal.reason() == null ? "" : proposal.reason();
        String reason = rawReason.trim();
        if (reason.isEmpty() || reason.length() > 200
                || rawReason.codePoints().anyMatch(Character::isISOControl)) {
            throw rule("申请原因必须为1到200个非控制字符。");
        }
        BigDecimal days;
        if (proposal.halfDay() == HalfDay.AM || proposal.halfDay() == HalfDay.PM) {
            if (!proposal.startDate().equals(proposal.endDate()) || isWeekend(proposal.startDate())) {
                throw rule("半天年假仅支持工作日单日申请。");
            }
            days = new BigDecimal("0.5");
        } else {
            long weekdays = 0;
            for (LocalDate day = proposal.startDate(); !day.isAfter(proposal.endDate()); day = day.plusDays(1)) {
                if (!isWeekend(day)) {
                    weekdays++;
                }
            }
            days = BigDecimal.valueOf(weekdays).setScale(1);
        }
        if (days.compareTo(BigDecimal.ZERO) <= 0) {
            throw rule("申请日期范围不包含有效工作日。");
        }
        return new ValidatedLeave(reason, days);
    }

    private boolean isWeekend(LocalDate date) {
        return date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY;
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
        log.info("action_audit traceId={} originTraceId={} actionRef={} actionType={} "
                        + "statusFrom={} statusTo={} resultCode={} requestRef={} createdAt={} completedAt={}",
                traceId, action.originTraceId(), auditRef(action.actionId()), action.actionType(), from, to,
                resultCode, auditRef(requestId), action.createdAt(), completedAt);
    }

    static String auditRef(String rawId) {
        if (rawId == null) {
            return "-";
        }
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(rawId.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest, 0, 6);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }

    private record ValidatedLeave(String reason, BigDecimal days) {}
}
