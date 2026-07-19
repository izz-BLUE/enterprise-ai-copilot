package com.fantuan.copilot.gateway.leave;

import com.fantuan.copilot.model.action.HalfDay;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

public record LeaveSubmission(
        String sourceActionId,
        String employeeId,
        LocalDate startDate,
        LocalDate endDate,
        HalfDay halfDay,
        BigDecimal days,
        Instant submittedAt) {
}
