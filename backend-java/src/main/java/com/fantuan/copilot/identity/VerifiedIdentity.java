package com.fantuan.copilot.identity;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoRole;

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

    public static VerifiedIdentity from(DemoIdentity identity) {
        AuthRole role = identity.role() == DemoRole.ADMIN ? AuthRole.ADMIN : AuthRole.EMPLOYEE;
        return new VerifiedIdentity(identity.userId(), identity.userId(), identity.employeeId(),
                identity.displayName(), role, true, Source.DEMO);
    }

    public DemoIdentity asDemoIdentity() {
        DemoRole demoRole = role == AuthRole.ADMIN ? DemoRole.ADMIN : DemoRole.EMPLOYEE;
        return new DemoIdentity(userId, employeeId, displayName, demoRole);
    }

    public enum Source {
        JWT,
        DEMO
    }
}
