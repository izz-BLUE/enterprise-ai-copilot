package com.fantuan.copilot.model.action;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * 报销单（Java Expense Domain Source of Truth，V2 §二十一）。
 *
 * sourceActionId 与 business_action.action_id 1:1 关联且 UNIQUE ——
 * 最终业务副作用的幂等防线（同一确认动作不会创建两笔报销）。
 */
public record ExpenseClaim(
        String expenseId,
        String sourceActionId,
        String employeeId,
        String tripId,
        String costCenter,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        ExpenseStatus status,
        Instant createdAt,
        Instant updatedAt,
        String externalProvider,
        String externalRequestId,
        String externalWaitId,
        Instant externalLastCheckedAt,
        Instant externalResumeLastAttemptAt,
        Instant externalResumeCompletedAt) {

    public ExpenseClaim(String expenseId, String sourceActionId, String employeeId, String tripId,
                        String costCenter, BigDecimal claimedAmount, BigDecimal reimbursableAmount,
                        ExpenseStatus status, Instant createdAt, Instant updatedAt) {
        this(expenseId, sourceActionId, employeeId, tripId, costCenter, claimedAmount,
                reimbursableAmount, status, createdAt, updatedAt,
                null, null, null, null, null, null);
    }

    public ExpenseClaim(String expenseId, String sourceActionId, String employeeId, String tripId,
                        String costCenter, BigDecimal claimedAmount, BigDecimal reimbursableAmount,
                        ExpenseStatus status, Instant createdAt, Instant updatedAt,
                        String externalProvider, String externalRequestId, String externalWaitId) {
        this(expenseId, sourceActionId, employeeId, tripId, costCenter, claimedAmount,
                reimbursableAmount, status, createdAt, updatedAt, externalProvider,
                externalRequestId, externalWaitId, null, null, null);
    }
}
