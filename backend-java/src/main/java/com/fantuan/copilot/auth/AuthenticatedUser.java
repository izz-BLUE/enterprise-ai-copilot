package com.fantuan.copilot.auth;

/**
 * Trusted request principal built from a verified JWT.
 * enabled is the login-time verification result; it is intentionally not a JWT claim.
 */
public record AuthenticatedUser(
        String userId,
        String username,
        String employeeId,
        String displayName,
        AuthRole role,
        boolean enabled) {
}
