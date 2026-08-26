package com.fantuan.copilot.dto.action;

import java.util.List;

/** expense_status_tool 的最近几笔响应（V2 §二十四 可选 /recent）。 */
public record ExpenseListResponse(
        String employeeId,
        int total,
        List<ExpenseStatusResponse> items) {
}
