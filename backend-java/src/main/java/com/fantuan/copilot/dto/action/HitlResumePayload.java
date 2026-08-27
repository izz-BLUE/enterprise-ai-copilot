package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;

/** Java-authoritative result sent to Python's internal HITL resume endpoint. */
public record HitlResumePayload(
        @JsonProperty("schema_version") int schemaVersion,
        @JsonProperty("wait_id") String waitId,
        @JsonProperty("execution_id") String executionId,
        HitlDecision decision,
        @JsonProperty("action_id") String actionId,
        @JsonProperty("action_type") BusinessActionType actionType,
        @JsonProperty("action_status") ActionStatus actionStatus,
        @JsonProperty("request_id") String requestId,
        String message) {

    public enum HitlDecision {
        CONFIRMED, CANCELLED, EXPIRED, REJECTED
    }
}
