package com.fantuan.copilot.gateway.leave;

import java.time.LocalDate;

public interface LeaveExecutionGateway {
    boolean hasConflict(String employeeId, LocalDate startDate, LocalDate endDate);

    LeaveExecutionResult submit(LeaveSubmission submission);
}
