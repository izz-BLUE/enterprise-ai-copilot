package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;

import java.time.LocalDate;

public record AnnualLeaveActionProposal(
        @JsonAlias("action_type") BusinessActionType actionType,
        @JsonAlias("start_date") LocalDate startDate,
        @JsonAlias("end_date") LocalDate endDate,
        String reason,
        @JsonAlias("half_day") HalfDay halfDay) {
}
