package com.fantuan.copilot.security;

/** Mock OA webhook 未通过 HMAC 新鲜度边界时抛出。 */
public class MockOaWebhookAuthenticationException extends RuntimeException {
    public MockOaWebhookAuthenticationException(String message) {
        super(message);
    }
}
