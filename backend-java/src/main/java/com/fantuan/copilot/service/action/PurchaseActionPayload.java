package com.fantuan.copilot.service.action;

import java.math.BigDecimal;

/** PendingAction.action_payload_json 的采购 canonical 结构。 */
public record PurchaseActionPayload(
        int schemaVersion,
        String itemName,
        BigDecimal requestedBudget,
        String justification,
        BigDecimal availableBudget,
        String policyResult) {
}
