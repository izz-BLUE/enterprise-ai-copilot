package com.fantuan.copilot.service;

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

    public boolean isAdmin(String presentedToken) {
        if (configuredToken == null || configuredToken.isBlank()) {
            return true;
        }
        if (presentedToken == null) {
            return false;
        }
        return MessageDigest.isEqual(
                configuredToken.getBytes(StandardCharsets.UTF_8),
                presentedToken.getBytes(StandardCharsets.UTF_8));
    }
}
