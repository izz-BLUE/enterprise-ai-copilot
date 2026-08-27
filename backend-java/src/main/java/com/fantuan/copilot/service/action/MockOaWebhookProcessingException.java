package com.fantuan.copilot.service.action;

/** Raised when the authenticated notification cannot be reconciled safely. */
public class MockOaWebhookProcessingException extends RuntimeException {
    public MockOaWebhookProcessingException(String message) {
        super(message);
    }

    public MockOaWebhookProcessingException(String message, Throwable cause) {
        super(message, cause);
    }
}
