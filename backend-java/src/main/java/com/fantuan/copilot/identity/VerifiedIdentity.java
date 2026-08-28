package com.fantuan.copilot.identity;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;

public record VerifiedIdentity(
        String userId,
        String username,
        String employeeId,
        String displayName,
        AuthRole role,
        boolean enabled,
        Source source) {

    public static VerifiedIdentity from(AuthenticatedUser user) {
        return new VerifiedIdentity(user.userId(), user.username(), user.employeeId(),
                user.displayName(), user.role(), user.enabled(), Source.JWT);
    }

    public enum Source {
        JWT
    }
}
