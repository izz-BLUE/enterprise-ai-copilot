package com.fantuan.copilot.gateway.expense;

import java.time.Instant;

public record ExpenseExecutionResult(String expenseId, Instant submittedAt) {
}
