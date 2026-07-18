package com.fantuan.copilot.gateway.leave;

import java.time.Instant;

public record LeaveExecutionResult(String requestId, Instant submittedAt) {
}
