package com.fantuan.copilot.auth;

import com.fantuan.copilot.identity.VerifiedIdentity;

/**
 * Server-side policy for the intentionally public demo identity.
 *
 * The public demo remains an EMPLOYEE so it can use ordinary authenticated
 * Agent/RAG and read-only capabilities.  It is not granted controlled
 * business-action capability merely because the global action switch and
 * optional server-side hardening token are enabled.
 */
public final class DemoAuthPolicy {
    public static final String PUBLIC_DEMO_USER_ID = "U10000";
    public static final String PUBLIC_DEMO_USERNAME = "demo";
    public static final String PUBLIC_DEMO_EMPLOYEE_ID = "E10000";

    private DemoAuthPolicy() {
    }

    public static boolean isPublicDemo(VerifiedIdentity identity) {
        return identity != null
                && PUBLIC_DEMO_USER_ID.equals(identity.userId())
                && PUBLIC_DEMO_USERNAME.equals(identity.username());
    }

    public static boolean mayUseBusinessActions(VerifiedIdentity identity) {
        return identity != null
                && identity.employeeId() != null
                && !identity.employeeId().isBlank()
                && !isPublicDemo(identity);
    }
}
