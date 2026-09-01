package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.util.regex.Pattern;

/** 仅由 Java 内部注册路径使用的 Python checkpoint marker。 */
@JsonIgnoreProperties(ignoreUnknown = false)
public record HitlWaitMarker(
        @JsonAlias("schema_version") Integer schemaVersion,
        String kind,
        @JsonAlias("wait_id") String waitId,
        @JsonAlias("execution_id") String executionId,
        @JsonAlias("action_type") BusinessActionType actionType) {
    private static final Pattern WAIT_ID = Pattern.compile("wait_[0-9a-f]{64}");
    private static final Pattern EXECUTION_ID = Pattern.compile("ex_[0-9a-f]{32}");

    public boolean structurallyValid() {
        return schemaVersion != null && schemaVersion == 1
                && "BUSINESS_ACTION_CONFIRMATION".equals(kind)
                && waitId != null && WAIT_ID.matcher(waitId).matches()
                && executionId != null && EXECUTION_ID.matcher(executionId).matches()
                && (actionType == BusinessActionType.ANNUAL_LEAVE_REQUEST
                    || actionType == BusinessActionType.EXPENSE_CLAIM
                    || actionType == BusinessActionType.PURCHASE_REQUEST);
    }
}
