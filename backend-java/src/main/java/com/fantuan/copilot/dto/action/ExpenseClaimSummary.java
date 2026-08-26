package com.fantuan.copilot.dto.action;

import java.math.BigDecimal;
import java.util.List;

/**
 * 报销草稿对外摘要（V2 §二十五：前端按 action.type 分发渲染）。
 *
 * 与 AnnualLeaveSummary 互不关联；不携带 trusted identity 字段
 * （employee_id / user_id 等由 Java 侧注入，前端只读展示）。
 */
public record ExpenseClaimSummary(
        String tripId,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        String costCenter,
        String reason,
        int itemCount,
        List<String> invoiceIds) {
}
