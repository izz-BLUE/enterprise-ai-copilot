package com.fantuan.copilot.dto.action;

import java.math.BigDecimal;

/** PendingAction 卡片使用的采购业务摘要。 */
public record PurchaseSummary(
        String itemName,
        BigDecimal requestedBudget,
        String justification,
        BigDecimal availableBudget,
        String policyResult) {
}
