package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.math.BigDecimal;

/** P4-3 采购申请 Proposal；只承载业务字段，不承载身份或确认字段。 */
public record PurchaseActionProposal(
        @JsonAlias("action_type") BusinessActionType actionType,
        @JsonAlias("item_name") String itemName,
        @JsonAlias("requested_budget") BigDecimal requestedBudget,
        String justification,
        @JsonAlias("available_budget") BigDecimal availableBudget,
        @JsonAlias("policy_result") String policyResult
) implements BusinessActionProposal {
}
