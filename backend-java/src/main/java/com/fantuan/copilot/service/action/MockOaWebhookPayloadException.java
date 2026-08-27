package com.fantuan.copilot.service.action;

/** Raised when an authenticated Mock OA webhook is not a supported strict envelope. */
public class MockOaWebhookPayloadException extends RuntimeException {
    public MockOaWebhookPayloadException(String message, Throwable cause) {
        super(message, cause);
    }

    public MockOaWebhookPayloadException(String message) {
        super(message);
    }
}
