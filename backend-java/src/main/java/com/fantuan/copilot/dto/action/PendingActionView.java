package com.fantuan.copilot.dto.action;

import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.time.Instant;

public record PendingActionView(
        String actionId,
        BusinessActionType type,
        ActionStatus status,
        String title,
        AnnualLeaveSummary summary,
        String confirmationNonce,
        Instant expiresAt,
        boolean confirmationRequired) {
}
