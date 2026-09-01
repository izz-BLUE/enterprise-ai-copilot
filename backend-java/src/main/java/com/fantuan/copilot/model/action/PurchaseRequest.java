package com.fantuan.copilot.model.action;

import java.math.BigDecimal;
import java.time.Instant;

/** P4-3 最小采购申请事实表模型。 */
public record PurchaseRequest(
        String requestId,
        String sourceActionId,
        String ownerUserId,
        String employeeId,
        String itemName,
        BigDecimal requestedBudget,
        String justification,
        PurchaseRequestStatus status,
        Instant createdAt) {
}
