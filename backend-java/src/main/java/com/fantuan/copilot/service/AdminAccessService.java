package com.fantuan.copilot.service;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.identity.VerifiedIdentity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Service
public class AdminAccessService {
    private final String configuredToken;

    public AdminAccessService(@Value("${admin.token:}") String configuredToken) {
        this.configuredToken = configuredToken;
    }

    /**
     * Validates the optional server-side hardening credential.  An empty
     * configuration is not a valid token when the hardening gate is enabled;
     * the caller may explicitly disable that gate and use identity policy
     * instead.
     */
    public boolean isAdmin(String presentedToken) {
        if (configuredToken == null || configuredToken.isBlank()) {
            return false;
        }
        if (presentedToken == null) {
            return false;
        }
        return MessageDigest.isEqual(
                configuredToken.getBytes(StandardCharsets.UTF_8),
                presentedToken.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Browser-facing administration is authorized from the verified JWT
     * identity.  The role is reconstructed by Java's JWT converter and is not
     * read from request parameters, localStorage, or a client-supplied role.
     */
    public boolean isAdminIdentity(VerifiedIdentity identity) {
        return identity != null && identity.enabled() && identity.role() == AuthRole.ADMIN;
    }
}
