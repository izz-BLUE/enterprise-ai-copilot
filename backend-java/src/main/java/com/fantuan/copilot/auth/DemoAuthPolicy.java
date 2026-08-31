package com.fantuan.copilot.auth;

import com.fantuan.copilot.identity.VerifiedIdentity;

/**
 * 面向有意公开的 Demo 身份的服务端策略。
 *
 * 公开 Demo 保持 EMPLOYEE 身份，因此可以使用普通认证后的 Agent/RAG 和只读
 * capability。即使全局动作开关和可选的服务端 hardening token 已启用，也不会
 * 仅因此获得受控业务动作 capability。
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
