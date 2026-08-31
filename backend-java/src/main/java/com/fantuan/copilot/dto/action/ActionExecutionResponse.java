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
        String traceId,
        PendingActionView nextPendingAction) {
    /** 兼容单动作响应契约的构造方法。 */
    public ActionExecutionResponse(String actionId,
                                   BusinessActionType type,
                                   ActionStatus status,
                                   String requestId,
                                   String message,
                                   boolean replayed,
                                   Instant completedAt,
                                   String originTraceId,
                                   String traceId) {
        this(actionId, type, status, requestId, message, replayed, completedAt,
                originTraceId, traceId, null);
    }

    public ActionExecutionResponse replayedFor(String currentTraceId) {
        return new ActionExecutionResponse(actionId, type, status, requestId, message, true,
                completedAt, originTraceId, currentTraceId, nextPendingAction);
    }

    public ActionExecutionResponse withNextPendingAction(PendingActionView nextAction) {
        return new ActionExecutionResponse(actionId, type, status, requestId, message, replayed,
                completedAt, originTraceId, traceId, nextAction);
    }
}
