package com.fantuan.copilot.service.action;

/** 已认证通知无法安全 reconciliation 时抛出。 */
public class MockOaWebhookProcessingException extends RuntimeException {
    public MockOaWebhookProcessingException(String message) {
        super(message);
    }

    public MockOaWebhookProcessingException(String message, Throwable cause) {
        super(message, cause);
    }
}
