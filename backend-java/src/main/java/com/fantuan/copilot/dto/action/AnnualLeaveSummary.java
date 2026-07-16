package com.fantuan.copilot.dto.action;

import com.fantuan.copilot.model.action.HalfDay;

import java.math.BigDecimal;
import java.time.LocalDate;

public record AnnualLeaveSummary(
        String employee,
        LocalDate startDate,
        LocalDate endDate,
        HalfDay halfDay,
        BigDecimal days,
        String reason,
        BigDecimal remainingBalanceBefore,
        BigDecimal remainingBalanceAfter) {
}
