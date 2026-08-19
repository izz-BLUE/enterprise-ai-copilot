package com.fantuan.copilot.dto.auth;

import com.fantuan.copilot.auth.AppUser;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.auth.AuthRole;

public record AuthUserResponse(
        String userId,
        String username,
        String employeeId,
        String displayName,
        AuthRole role,
        boolean enabled) {

    public static AuthUserResponse from(AppUser user) {
        return new AuthUserResponse(user.userId(), user.username(), user.employeeId(),
                user.displayName(), user.role(), user.enabled());
    }

    public static AuthUserResponse from(AuthenticatedUser user) {
        return new AuthUserResponse(user.userId(), user.username(), user.employeeId(),
                user.displayName(), user.role(), user.enabled());
    }
}
