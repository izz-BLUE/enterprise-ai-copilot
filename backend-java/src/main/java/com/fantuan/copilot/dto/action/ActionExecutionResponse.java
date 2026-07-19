package com.fantuan.copilot.dto.action;

import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.time.Instant;

public record ActionExecutionResponse(
        String actionId,
        BusinessActionType type,
        ActionStatus status,
        String requestId,
        String message,
        boolean replayed,
        Instant completedAt,
        String originTraceId,
        String traceId) {
    public ActionExecutionResponse replayedFor(String currentTraceId) {
        return new ActionExecutionResponse(actionId, type, status, requestId, message, true,
                completedAt, originTraceId, currentTraceId);
    }
}
