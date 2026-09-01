package com.fantuan.copilot.gateway.purchase;

import java.time.Instant;

public record PurchaseExecutionResult(String requestId, Instant submittedAt) {
}
