package com.fantuan.copilot.gateway.purchase;

import java.math.BigDecimal;
import java.time.Instant;

public record PurchaseSubmission(
        String sourceActionId,
        String ownerUserId,
        String employeeId,
        String itemName,
        BigDecimal requestedBudget,
        String justification,
        Instant submittedAt) {
}
