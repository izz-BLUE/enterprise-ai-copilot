package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseItem;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

/**
 * 报销提交输入（V2 §二十三）。
 *
 * sourceActionId 来自 business_action.action_id（1:1）；
 * 其余业务字段（tripId / costCenter / claimedAmount / reimbursableAmount /
 * items）由 ExpenseClaimActionHandler 的 deterministic 逻辑组装。
 */
public record ExpenseSubmission(
        String sourceActionId,
        String employeeId,
        String tripId,
        String costCenter,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        List<ExpenseItem> items,
        Instant submittedAt) {
}
