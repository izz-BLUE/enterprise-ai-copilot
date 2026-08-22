package com.fantuan.copilot.service.memory;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.util.Base64;

/**
 * Java -> Python -> Java Memory write scope.
 *
 * The scope is issued only after the outer LangGraph request has resolved a
 * verified identity. Python may carry it back to Java, but cannot change the
 * user or conversation encoded in the signed value. The signing key is the
 * existing JAVA_INTERNAL_TOKEN; no second service credential is introduced.
 */
@Service
public class MemoryWriteScopeService {

    private static final String VERSION = "v1";
    private static final long TTL_SECONDS = 120;
    private static final int NONCE_BYTES = 16;
    private static final SecureRandom RANDOM = new SecureRandom();

    private final String internalToken;
    private final Clock clock;

    @Autowired
    public MemoryWriteScopeService(
            @Value("${leave.read.internal-token:}") String internalToken,
            Clock clock) {
        this.internalToken = internalToken == null ? "" : internalToken;
        this.clock = clock;
    }

    /**
     * Issues an opaque, short-lived scope only for an already verified owner.
     * Blank internal-token configuration intentionally disables issuing.
     */
    public String issue(String userId, String conversationId) {
        if (internalToken.isBlank() || userId == null || userId.isBlank()
                || conversationId == null || conversationId.isBlank()) {
            return null;
        }
        long expiresAt = clock.instant().plusSeconds(TTL_SECONDS).getEpochSecond();
        String payload = VERSION + "."
                + encode(userId) + "."
                + encode(conversationId) + "."
                + expiresAt + "."
                + encode(randomNonce());
        return payload + "." + encode(sign(payload));
    }

    public boolean matchesInternalToken(String presented) {
        if (internalToken.isBlank() || presented == null) {
            return false;
        }
        return MessageDigest.isEqual(
                internalToken.getBytes(StandardCharsets.UTF_8),
                presented.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Verifies signature and expiry, returning only the server-issued scope.
     * Any malformed or forged value is rejected fail-closed.
     */
    public Scope verify(String token) {
        if (internalToken.isBlank() || token == null || token.isBlank()) {
            throw new IllegalArgumentException("Memory write scope 不可用");
        }
        String[] parts = token.split("\\.", -1);
        if (parts.length != 6 || !VERSION.equals(parts[0])) {
            throw new IllegalArgumentException("Memory write scope 格式非法");
        }
        String payload = String.join(".", parts[0], parts[1], parts[2], parts[3], parts[4]);
        byte[] presentedSignature;
        try {
            presentedSignature = decode(parts[5]);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("Memory write scope 签名非法", exception);
        }
        if (!MessageDigest.isEqual(sign(payload), presentedSignature)) {
            throw new IllegalArgumentException("Memory write scope 签名校验失败");
        }

        long expiresAt;
        try {
            expiresAt = Long.parseLong(parts[3]);
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("Memory write scope 过期时间非法", exception);
        }
        if (clock.instant().getEpochSecond() >= expiresAt) {
            throw new IllegalArgumentException("Memory write scope 已过期");
        }

        try {
            String userId = new String(decode(parts[1]), StandardCharsets.UTF_8);
            String conversationId = new String(decode(parts[2]), StandardCharsets.UTF_8);
            if (userId.isBlank() || conversationId.isBlank()) {
                throw new IllegalArgumentException("Memory write scope owner 为空");
            }
            return new Scope(userId, conversationId);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("Memory write scope owner 非法", exception);
        }
    }

    private byte[] randomNonce() {
        byte[] nonce = new byte[NONCE_BYTES];
        RANDOM.nextBytes(nonce);
        return nonce;
    }

    private byte[] sign(String payload) {
        try {
            var mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(
                    internalToken.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("Memory write scope 签名失败", exception);
        }
    }

    private static String encode(String value) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String encode(byte[] value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    private static byte[] decode(String value) {
        return Base64.getUrlDecoder().decode(value);
    }

    public record Scope(String userId, String conversationId) {
    }
}
