package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.AnnualLeaveSummary;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.UUID;

@Service
public class BusinessActionService {
    private static final Logger log = LoggerFactory.getLogger(BusinessActionService.class);
    private static final String EMPLOYEE_ID = "DEMO-001";
    private static final String DISPLAY_NAME = "Demo User";

    private final BusinessActionProperties properties;
    private final AdminAccessService adminAccessService;
    private final PendingActionRepository repository;
    private final ActionNonceService nonceService;
    private final LeaveSandboxService sandbox;
    private final Clock clock;
    private final SecureRandom secureRandom = new SecureRandom();

    public BusinessActionService(BusinessActionProperties properties,
                                 AdminAccessService adminAccessService,
                                 PendingActionRepository repository,
                                 ActionNonceService nonceService,
                                 LeaveSandboxService sandbox,
                                 Clock clock) {
        this.properties = properties;
        this.adminAccessService = adminAccessService;
        this.repository = repository;
        this.nonceService = nonceService;
        this.sandbox = sandbox;
        this.clock = clock;
    }

    public LocalDate businessDate() { return LocalDate.now(clock); }

    public boolean isAllowed(String presentedToken) {
        return properties.isEnabled()
                && (!properties.isRequireAdmin() || adminAccessService.isAdmin(presentedToken));
    }

    public PendingActionView createPending(AnnualLeaveActionProposal proposal,
                                           String originTraceId,
                                           String presentedToken) {
        requireEnabledAndAdmin(presentedToken);
        ValidatedLeave validated = validate(proposal);
        LeaveSandboxService.Preview preview = sandbox.preview(
                EMPLOYEE_ID, proposal.startDate(), proposal.endDate(), validated.days());
        ActionNonceService.Nonce nonce = nonceService.create();
        Instant now = clock.instant();
        String actionId = randomActionId();
        PendingAction action = new PendingAction(
                actionId, BusinessActionType.ANNUAL_LEAVE_REQUEST, originTraceId,
                EMPLOYEE_ID, DISPLAY_NAME, proposal.startDate(), proposal.endDate(),
                proposal.halfDay(), validated.reason(), validated.days(),
                preview.balanceBefore(), preview.balanceAfter(), nonce.digest(), now,
                now.plusSeconds(properties.getTtlSeconds()));
        repository.saveNew(action);
        audit(originTraceId, action, null, ActionStatus.PENDING_CONFIRMATION, "ACTION_CREATED", null);
        return new PendingActionView(actionId, action.actionType(), action.status(),
                "提交模拟年假申请",
                new AnnualLeaveSummary(DISPLAY_NAME, action.startDate(), action.endDate(),
                        action.halfDay(), action.days(), action.reason(),
                        action.balanceBefore(), action.balanceAfter()),
                nonce.plaintext(), action.expiresAt(), true);
    }

    public ActionExecutionResponse confirm(String actionId, String confirmationNonce,
                                           String idempotencyKey, String presentedToken,
                                           String traceId) {
        requireEnabledAndAdmin(presentedToken);
        UUID key = parseIdempotencyKey(idempotencyKey);
        PendingAction action = find(actionId);
        synchronized (action) {
            verifyNonce(action, confirmationNonce);
            if (action.status() == ActionStatus.EXPIRED) {
                throw error(HttpStatus.GONE, "ACTION_EXPIRED",
                        "该申请草稿已过期，请重新生成。", action);
            }
            if (action.status() == ActionStatus.SUCCEEDED) {
                return action.completedResponse().replayedFor(traceId);
            }
            if (action.status() == ActionStatus.PROCESSING) {
                throw error(HttpStatus.CONFLICT, "ACTION_IN_PROGRESS", "申请正在处理中。", action);
            }
            if (action.status() != ActionStatus.PENDING_CONFIRMATION) {
                throw error(HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT", "当前状态不能确认。", action);
            }
            action.markProcessing(key);
            audit(traceId, action, ActionStatus.PENDING_CONFIRMATION,
                    ActionStatus.PROCESSING, "CONFIRM_ACCEPTED", null);
        }

        try {
            LeaveRequest request = sandbox.submit(action);
            Instant completedAt = clock.instant();
            ActionExecutionResponse response = new ActionExecutionResponse(
                    action.actionId(), action.actionType(), ActionStatus.SUCCEEDED,
                    request.requestId(), "模拟年假申请已提交。", false, completedAt,
                    action.originTraceId(), traceId);
            synchronized (action) {
                action.markSucceeded(request.requestId(), response, completedAt);
            }
            repository.maintainBounds();
            audit(traceId, action, ActionStatus.PROCESSING,
                    ActionStatus.SUCCEEDED, "ACTION_SUCCEEDED", request.requestId());
            return response;
        } catch (ActionException exception) {
            synchronized (action) {
                action.markFailed("ACTION_STALE", clock.instant());
            }
            repository.maintainBounds();
            audit(traceId, action, ActionStatus.PROCESSING,
                    ActionStatus.FAILED, "ACTION_STALE", null);
            throw error(HttpStatus.CONFLICT, "ACTION_STALE",
                    "申请状态已变化，请重新生成草稿。", action);
        } catch (RuntimeException exception) {
            synchronized (action) {
                action.markFailed("ACTION_INTERNAL_ERROR", clock.instant());
            }
            repository.maintainBounds();
            audit(traceId, action, ActionStatus.PROCESSING,
                    ActionStatus.FAILED, "ACTION_INTERNAL_ERROR", null);
            throw error(HttpStatus.INTERNAL_SERVER_ERROR, "ACTION_INTERNAL_ERROR",
                    "模拟申请处理失败。", action);
        }
    }

    public ActionExecutionResponse cancel(String actionId, String confirmationNonce,
                                          String presentedToken, String traceId) {
        requireEnabledAndAdmin(presentedToken);
        PendingAction action = find(actionId);
        ActionExecutionResponse response;
        synchronized (action) {
            verifyNonce(action, confirmationNonce);
            if (action.status() == ActionStatus.EXPIRED) {
                throw error(HttpStatus.GONE, "ACTION_EXPIRED",
                        "该申请草稿已过期，请重新生成。", action);
            }
            if (action.status() == ActionStatus.CANCELLED) {
                return cancelledResponse(action, traceId, true);
            }
            if (action.status() != ActionStatus.PENDING_CONFIRMATION) {
                throw error(HttpStatus.CONFLICT, "ACTION_STATE_CONFLICT", "当前状态不能取消。", action);
            }
            ActionStatus from = action.status();
            action.markCancelled(clock.instant());
            audit(traceId, action, from, ActionStatus.CANCELLED, "ACTION_CANCELLED", null);
            response = cancelledResponse(action, traceId, false);
        }
        repository.maintainBounds();
        return response;
    }

    private ActionExecutionResponse cancelledResponse(PendingAction action,
                                                       String traceId, boolean replayed) {
        return new ActionExecutionResponse(action.actionId(), action.actionType(),
                ActionStatus.CANCELLED, null, "申请草稿已取消。", replayed,
                action.completedAt(), action.originTraceId(), traceId);
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

    private PendingAction find(String actionId) {
        return repository.find(actionId).orElseThrow(() -> new ActionException(
                HttpStatus.NOT_FOUND, "ACTION_NOT_FOUND", "未找到申请草稿。", actionId, null));
    }

    private void verifyNonce(PendingAction action, String plaintext) {
        if (!nonceService.matches(plaintext, action.confirmationNonceDigest())) {
            throw error(HttpStatus.FORBIDDEN, "INVALID_CONFIRMATION_NONCE",
                    "确认凭据无效。", action);
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

    private void audit(String traceId, PendingAction action, ActionStatus from,
                       ActionStatus to, String resultCode, String requestId) {
        log.info("action_audit traceId={} originTraceId={} actionId={} actionType={} "
                        + "statusFrom={} statusTo={} resultCode={} requestId={} createdAt={} completedAt={}",
                traceId, action.originTraceId(), action.actionId(), action.actionType(), from, to,
                resultCode, requestId, action.createdAt(), action.completedAt());
    }

    private record ValidatedLeave(String reason, BigDecimal days) {}
}
