package com.fantuan.copilot.security;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Clock;

/** Verifies the exact Mock OA webhook bytes before JSON parsing or business work. */
@Component
public class MockOaWebhookVerifier {
    private static final long MAX_REPLAY_WINDOW_SECONDS = 300;

    private final String secret;
    private final long replayWindowSeconds;
    private final Clock clock;

    @Autowired
    public MockOaWebhookVerifier(
            @Value("${external.approval.mock-oa.webhook-secret:}") String secret,
            @Value("${external.approval.mock-oa.webhook-replay-window-seconds:300}") long replayWindowSeconds) {
        this(secret, replayWindowSeconds, Clock.systemUTC());
    }

    public MockOaWebhookVerifier(String secret, long replayWindowSeconds, Clock clock) {
        this.secret = secret == null ? "" : secret;
        this.replayWindowSeconds = Math.max(1, Math.min(replayWindowSeconds, MAX_REPLAY_WINDOW_SECONDS));
        this.clock = clock;
    }

    public void verify(byte[] rawBody, String timestampHeader, String signatureHeader) {
        long timestamp = parseTimestamp(timestampHeader);
        long now = clock.instant().getEpochSecond();
        if (timestamp < now - replayWindowSeconds || timestamp > now + replayWindowSeconds) {
            throw new MockOaWebhookAuthenticationException("stale webhook timestamp");
        }
        if (secret.isBlank() || signatureHeader == null
                || !signatureHeader.matches("v1=[0-9a-f]{64}")) {
            throw new MockOaWebhookAuthenticationException("invalid webhook signature");
        }

        byte[] body = rawBody == null ? new byte[0] : rawBody;
        byte[] expected = hmac(timestampHeader + ".", body);
        byte[] presented = signatureHeader.substring(3).getBytes(StandardCharsets.US_ASCII);
        if (!MessageDigest.isEqual(expected, presented)) {
            throw new MockOaWebhookAuthenticationException("invalid webhook signature");
        }
    }

    private long parseTimestamp(String timestampHeader) {
        if (timestampHeader == null || timestampHeader.isBlank()) {
            throw new MockOaWebhookAuthenticationException("missing webhook timestamp");
        }
        try {
            return Long.parseLong(timestampHeader);
        } catch (NumberFormatException exception) {
            throw new MockOaWebhookAuthenticationException("invalid webhook timestamp");
        }
    }

    private byte[] hmac(String timestampPrefix, byte[] rawBody) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            mac.update(timestampPrefix.getBytes(StandardCharsets.UTF_8));
            mac.update(rawBody);
            return toLowerHex(mac.doFinal()).getBytes(StandardCharsets.US_ASCII);
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("HMAC-SHA256 is unavailable", exception);
        }
    }

    private String toLowerHex(byte[] bytes) {
        StringBuilder hex = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            hex.append(String.format("%02x", value));
        }
        return hex.toString();
    }
}
