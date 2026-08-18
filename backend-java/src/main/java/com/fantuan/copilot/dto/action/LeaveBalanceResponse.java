package com.fantuan.copilot.dto.action;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * leave_balance_tool 只读返回：当前登录用户的年假余额。
 * employeeId 由调用方注入；此处仅做原样回传。
 */
public record LeaveBalanceResponse(
        String employeeId,
        BigDecimal annualBalance,
        Instant updatedAt) {
}