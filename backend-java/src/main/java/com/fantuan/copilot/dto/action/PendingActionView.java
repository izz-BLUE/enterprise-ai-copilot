package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.time.Instant;

/**
 * PendingAction 对外视图。
 *
 * V2 §二十五：summary 由 handler 的 buildSummary 产出，按 action.type 区分
 * （ANNUAL_LEAVE_REQUEST → AnnualLeaveSummary；EXPENSE_CLAIM →
 * ExpenseClaimSummary）。前端按 type 分发对应 summary 页面，业务摘要字段
 * 不塞进年假专属字段（V2 §十八 禁止造假字段）。
 */
public record PendingActionView(
        String actionId,
        BusinessActionType type,
        ActionStatus status,
        String title,
        @JsonInclude(JsonInclude.Include.NON_NULL) Object summary,
        String confirmationNonce,
        Instant expiresAt,
        boolean confirmationRequired) {
}
