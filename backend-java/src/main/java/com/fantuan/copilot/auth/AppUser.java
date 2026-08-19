package com.fantuan.copilot.auth;

import java.time.Instant;

public record AppUser(
        String userId,
        String username,
        String passwordHash,
        String employeeId,
        String displayName,
        AuthRole role,
        boolean enabled,
        Instant createdAt) {
}
