package com.fantuan.copilot.dto.action;

import com.fantuan.copilot.model.action.ExpenseStatus;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * expense_status_tool 的 Java 权威响应（V2 §二十四）。
 *
 * 由 Java ExpenseClaimRepository 构建；Java 是最终 Source of Truth。
 * 不携带 employee_id 之外的内部字段（前端/LLM 不需要）。
 */
public record ExpenseStatusResponse(
        String expenseId,
        ExpenseStatus status,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        String tripId,
        Instant submittedAt) {
}
