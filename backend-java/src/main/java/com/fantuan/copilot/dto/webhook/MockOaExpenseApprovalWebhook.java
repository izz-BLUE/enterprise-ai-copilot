package com.fantuan.copilot.dto.webhook;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/** 严格的通知封装；其中有意不包含审批 status。 */
@JsonIgnoreProperties(ignoreUnknown = false)
public record MockOaExpenseApprovalWebhook(
        String eventId,
        String eventType,
        String requestId) {

    public static final String EVENT_TYPE = "EXPENSE_APPROVAL_CHANGED";

    public void validate() {
        requireIdentifier(eventId, "eventId", 128);
        if (!EVENT_TYPE.equals(eventType)) {
            throw new IllegalArgumentException("unsupported webhook event type");
        }
        requireIdentifier(requestId, "requestId", 128);
    }

    private static void requireIdentifier(String value, String field, int maxLength) {
        if (value == null || value.isBlank() || value.length() > maxLength
                || !value.matches("^[A-Za-z0-9][A-Za-z0-9._:-]{0," + (maxLength - 1) + "}$")) {
            throw new IllegalArgumentException("invalid webhook " + field);
        }
    }
}
