package com.fantuan.copilot.gateway.leave;

import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.*;

class PostgresLeaveSandboxGatewayTest {
    @Test
    void generatesRequestIdAndWritesRepositoryUsingSubmissionOwner() {
        LeaveRequestRepository repository = mock(LeaveRequestRepository.class);
        when(repository.nextNumber()).thenReturn(7L);
        PostgresLeaveSandboxGateway gateway = new PostgresLeaveSandboxGateway(
                repository, new BusinessActionProperties());
        LeaveSubmission submission = new LeaveSubmission("act_source", "DEMO-002",
                LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE,
                new BigDecimal("1.0"), Instant.parse("2026-07-19T00:00:00Z"));

        LeaveExecutionResult result = gateway.submit(submission);

        assertEquals("LR-202607-000007", result.requestId());
        ArgumentCaptor<LeaveRequest> request = ArgumentCaptor.forClass(LeaveRequest.class);
        verify(repository).save(eq("act_source"), request.capture());
        assertEquals("DEMO-002", request.getValue().employeeId());
        assertEquals(submission.submittedAt(), request.getValue().createdAt());
    }
}
