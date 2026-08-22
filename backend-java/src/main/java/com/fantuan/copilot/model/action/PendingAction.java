package com.fantuan.copilot.model.action;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.UUID;

public final class PendingAction {
    private final String actionId;
    private final BusinessActionType actionType;
    private final String originTraceId;
    private final String ownerUserId;
    private final String conversationId;
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
    private final ActionStatus status;
    private final UUID idempotencyKey;
    private final String requestId;
    private final String executionMessage;
    private final String failureCode;
    private final Instant createdAt;
    private final Instant expiresAt;
    private final Instant completedAt;

    public PendingAction(String actionId, BusinessActionType actionType, String originTraceId,
                         String ownerUserId, String conversationId,
                         String employeeId, String displayName, LocalDate startDate,
                         LocalDate endDate, HalfDay halfDay, String reason, BigDecimal days,
                         BigDecimal balanceBefore, BigDecimal balanceAfter,
                         byte[] confirmationNonceDigest, ActionStatus status,
                         UUID idempotencyKey, String requestId, String executionMessage,
                         String failureCode, Instant createdAt, Instant expiresAt,
                         Instant completedAt) {
        this.actionId = actionId;
        this.actionType = actionType;
        this.originTraceId = originTraceId;
        this.ownerUserId = ownerUserId;
        this.conversationId = conversationId;
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
        this.status = status;
        this.idempotencyKey = idempotencyKey;
        this.requestId = requestId;
        this.executionMessage = executionMessage;
        this.failureCode = failureCode;
        this.createdAt = createdAt;
        this.expiresAt = expiresAt;
        this.completedAt = completedAt;
    }

    /**
     * 新动作工厂。ownerUserId / conversationId 是 Memory 生命周期收口的关联 key
     * （对应 ai_task_memory 的 user_id / conversation_id），允许为 null 表示
     * 历史数据或无 Memory 关联（收口时跳过）。
     */
    public static PendingAction pending(String actionId, BusinessActionType actionType,
                                        String originTraceId, String ownerUserId,
                                        String conversationId, String employeeId,
                                        String displayName, LocalDate startDate,
                                        LocalDate endDate, HalfDay halfDay, String reason,
                                        BigDecimal days, BigDecimal balanceBefore,
                                        BigDecimal balanceAfter, byte[] nonceDigest,
                                        Instant createdAt, Instant expiresAt) {
        return new PendingAction(actionId, actionType, originTraceId, ownerUserId, conversationId,
                employeeId, displayName, startDate, endDate, halfDay, reason, days, balanceBefore,
                balanceAfter, nonceDigest, ActionStatus.PENDING_CONFIRMATION, null, null, null, null,
                createdAt, expiresAt, null);
    }

    public String actionId() { return actionId; }
    public BusinessActionType actionType() { return actionType; }
    public String originTraceId() { return originTraceId; }
    /** Memory 收口 owner：ai_task_memory.user_id 维度；null = 无关联（历史数据）。 */
    public String ownerUserId() { return ownerUserId; }
    /** Memory 收口会话：ai_task_memory.conversation_id 维度；null = 无关联（历史数据）。 */
    public String conversationId() { return conversationId; }
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
    public ActionStatus status() { return status; }
    public UUID idempotencyKey() { return idempotencyKey; }
    public String requestId() { return requestId; }
    public String executionMessage() { return executionMessage; }
    public String failureCode() { return failureCode; }
    public Instant createdAt() { return createdAt; }
    public Instant expiresAt() { return expiresAt; }
    public Instant completedAt() { return completedAt; }
}
