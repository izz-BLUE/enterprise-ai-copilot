package com.fantuan.copilot.dto.action;

import com.fantuan.copilot.model.action.LeaveRequest;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

/**
 * leave_request_tool 只读返回：当前登录用户的请假记录（按 submitted_at 倒序）。
 * status 字段为模拟值（SUCCEEDED）：leave_request 表只持久化已成功执行的请求，
 * PendingAction 状态由 business_action 表维护；当前 Tool 不暴露 PendingAction。
 */
public record LeaveRequestListResponse(
        String employeeId,
        int total,
        List<LeaveRequestItem> items) {

    public record LeaveRequestItem(
            String requestId,
            String leaveType,
            LocalDate startDate,
            LocalDate endDate,
            String halfDay,
            BigDecimal days,
            Instant submittedAt,
            String status) {
    }

    public static LeaveRequestListResponse of(String employeeId, List<LeaveRequest> requests) {
        List<LeaveRequestItem> items = requests.stream()
                .map(r -> new LeaveRequestItem(
                        r.requestId(),
                        r.leaveType(),
                        r.startDate(),
                        r.endDate(),
                        r.halfDay().name(),
                        r.days(),
                        r.createdAt(),
                        "SUCCEEDED"))
                .toList();
        return new LeaveRequestListResponse(employeeId, items.size(), items);
    }
}