package com.fantuan.copilot.model.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.UUID;

public final class PendingAction {
    private final String actionId;
    private final BusinessActionType actionType;
    private final String originTraceId;
    private final String employeeId;
    private final String displayName;
    private final LocalDate startDate;
    private final LocalDate endDate;
    private final HalfDay halfDay;
    private final String reason;
    private final BigDecimal days;
    private final BigDecimal balanceBefore;
    private final BigDecimal balanceAfter;
    private final byte[] confirmationNonceDigest;
    private final Instant createdAt;
    private final Instant expiresAt;
    private ActionStatus status = ActionStatus.PENDING_CONFIRMATION;
    private Instant completedAt;
    private String requestId;
    private ActionExecutionResponse completedResponse;
    private UUID idempotencyKey;
    private String failureCode;

    public PendingAction(String actionId, BusinessActionType actionType, String originTraceId,
                         String employeeId, String displayName, LocalDate startDate,
                         LocalDate endDate, HalfDay halfDay, String reason, BigDecimal days,
                         BigDecimal balanceBefore, BigDecimal balanceAfter,
                         byte[] confirmationNonceDigest, Instant createdAt, Instant expiresAt) {
        this.actionId = actionId;
        this.actionType = actionType;
        this.originTraceId = originTraceId;
        this.employeeId = employeeId;
        this.displayName = displayName;
        this.startDate = startDate;
        this.endDate = endDate;
        this.halfDay = halfDay;
        this.reason = reason;
        this.days = days;
        this.balanceBefore = balanceBefore;
        this.balanceAfter = balanceAfter;
        this.confirmationNonceDigest = confirmationNonceDigest.clone();
        this.createdAt = createdAt;
        this.expiresAt = expiresAt;
    }

    public String actionId() { return actionId; }
    public BusinessActionType actionType() { return actionType; }
    public String originTraceId() { return originTraceId; }
    public String employeeId() { return employeeId; }
    public String displayName() { return displayName; }
    public LocalDate startDate() { return startDate; }
    public LocalDate endDate() { return endDate; }
    public HalfDay halfDay() { return halfDay; }
    public String reason() { return reason; }
    public BigDecimal days() { return days; }
    public BigDecimal balanceBefore() { return balanceBefore; }
    public BigDecimal balanceAfter() { return balanceAfter; }
    public byte[] confirmationNonceDigest() { return confirmationNonceDigest.clone(); }
    public Instant createdAt() { return createdAt; }
    public Instant expiresAt() { return expiresAt; }
    public ActionStatus status() { return status; }
    public Instant completedAt() { return completedAt; }
    public String requestId() { return requestId; }
    public ActionExecutionResponse completedResponse() { return completedResponse; }
    public UUID idempotencyKey() { return idempotencyKey; }
    public String failureCode() { return failureCode; }

    public void markExpired(Instant now) {
        if (status == ActionStatus.PENDING_CONFIRMATION && !now.isBefore(expiresAt)) {
            status = ActionStatus.EXPIRED;
            completedAt = now;
            failureCode = "ACTION_EXPIRED";
        }
    }

    public void markProcessing(UUID key) {
        status = ActionStatus.PROCESSING;
        idempotencyKey = key;
    }

    public void markSucceeded(String newRequestId, ActionExecutionResponse response, Instant now) {
        status = ActionStatus.SUCCEEDED;
        requestId = newRequestId;
        completedResponse = response;
        completedAt = now;
    }

    public void updateCompletedResponse(ActionExecutionResponse response) {
        completedResponse = response;
    }

    public void markFailed(String code, Instant now) {
        status = ActionStatus.FAILED;
        failureCode = code;
        completedAt = now;
    }

    public void markCancelled(Instant now) {
        status = ActionStatus.CANCELLED;
        completedAt = now;
    }
}
