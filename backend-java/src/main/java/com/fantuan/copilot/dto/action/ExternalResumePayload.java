package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.util.Objects;

/** 发送给 Python external resume 的 Java 权威 ExpenseClaim 终态结果。 */
public record ExternalResumePayload(
        @JsonProperty("schema_version") int schemaVersion,
        @JsonProperty("wait_id") String waitId,
        @JsonProperty("execution_id") String executionId,
        @JsonProperty("action_type") BusinessActionType actionType,
        @JsonProperty("request_id") String requestId,
        Decision decision,
        Decision status,
        String message) {

    public ExternalResumePayload {
        if (schemaVersion != 1) {
            throw new IllegalArgumentException("Unsupported external resume schema version");
        }
        if (actionType != BusinessActionType.EXPENSE_CLAIM) {
            throw new IllegalArgumentException("External resume requires EXPENSE_CLAIM");
        }
        if (decision == null || status == null || decision != status) {
            throw new IllegalArgumentException("External resume decision and status must match");
        }
        if (waitId == null || waitId.isBlank() || executionId == null || executionId.isBlank()
                || requestId == null || requestId.isBlank()
                || message == null || message.isBlank()) {
            throw new IllegalArgumentException("External resume payload fields are required");
        }
        Objects.requireNonNull(message, "message");
    }

    public enum Decision {
        APPROVED, REJECTED
    }
}
