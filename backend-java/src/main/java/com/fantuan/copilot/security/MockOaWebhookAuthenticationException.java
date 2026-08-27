package com.fantuan.copilot.security;

/** Raised when a Mock OA webhook does not pass the HMAC freshness boundary. */
public class MockOaWebhookAuthenticationException extends RuntimeException {
    public MockOaWebhookAuthenticationException(String message) {
        super(message);
    }
}
