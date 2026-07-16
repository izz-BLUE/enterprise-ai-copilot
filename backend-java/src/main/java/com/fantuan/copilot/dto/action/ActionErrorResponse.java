package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fantuan.copilot.model.action.ActionStatus;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record ActionErrorResponse(
        String actionId,
        ActionStatus status,
        String errorCode,
        String message,
        String traceId) {
}
