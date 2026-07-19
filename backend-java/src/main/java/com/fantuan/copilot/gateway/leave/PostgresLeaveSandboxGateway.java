package com.fantuan.copilot.gateway.leave;

import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.stereotype.Component;

import java.time.YearMonth;
import java.time.format.DateTimeFormatter;

@Component
public class PostgresLeaveSandboxGateway implements LeaveExecutionGateway {
    private static final DateTimeFormatter REQUEST_MONTH = DateTimeFormatter.ofPattern("yyyyMM");

    private final LeaveRequestRepository requests;
    private final BusinessActionProperties properties;

    public PostgresLeaveSandboxGateway(LeaveRequestRepository requests,
                                       BusinessActionProperties properties) {
        this.requests = requests;
        this.properties = properties;
    }

    @Override
    public boolean hasConflict(String employeeId, java.time.LocalDate startDate,
                               java.time.LocalDate endDate) {
        return requests.hasConflict(employeeId, startDate, endDate);
    }

    @Override
    public LeaveExecutionResult submit(LeaveSubmission submission) {
        long number = requests.nextNumber();
        YearMonth month = YearMonth.from(submission.submittedAt().atZone(properties.zoneId()));
        String requestId = "LR-" + month.format(REQUEST_MONTH) + "-"
                + String.format("%06d", number);
        LeaveRequest request = new LeaveRequest(requestId, submission.employeeId(), "ANNUAL",
                submission.startDate(), submission.endDate(), submission.halfDay(),
                submission.days(), submission.submittedAt());
        requests.save(submission.sourceActionId(), request);
        return new LeaveExecutionResult(requestId, submission.submittedAt());
    }
}
