package com.fantuan.copilot.service.action;

import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;

@Service
public class ActionNonceService {
    private final SecureRandom secureRandom = new SecureRandom();

    public Nonce create() {
        byte[] bytes = new byte[32];
        secureRandom.nextBytes(bytes);
        String plaintext = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        return new Nonce(plaintext, digest(plaintext));
    }

    public boolean matches(String plaintext, byte[] expectedDigest) {
        if (plaintext == null || expectedDigest == null) {
            return false;
        }
        return MessageDigest.isEqual(digest(plaintext), expectedDigest);
    }

    private byte[] digest(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }

    public record Nonce(String plaintext, byte[] digest) {
        public Nonce {
            digest = digest.clone();
        }
        @Override
        public byte[] digest() { return digest.clone(); }
    }
}
