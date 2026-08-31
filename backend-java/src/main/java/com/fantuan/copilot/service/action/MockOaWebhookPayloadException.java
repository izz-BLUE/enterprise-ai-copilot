package com.fantuan.copilot.service.action;

/** 已认证的 Mock OA webhook 不是受支持的严格封装时抛出。 */
public class MockOaWebhookPayloadException extends RuntimeException {
    public MockOaWebhookPayloadException(String message, Throwable cause) {
        super(message, cause);
    }

    public MockOaWebhookPayloadException(String message) {
        super(message);
    }
}
