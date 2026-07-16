package com.fantuan.copilot.model.action;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

public record LeaveRequest(
        String requestId,
        String employeeId,
        String leaveType,
        LocalDate startDate,
        LocalDate endDate,
        HalfDay halfDay,
        BigDecimal days,
        Instant createdAt) {
}
