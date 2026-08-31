package com.fantuan.copilot.auth;

/**
 * 基于已验证 JWT 构建的可信请求主体。
 * enabled 是登录时的校验结果，有意不作为 JWT claim。
 */
public record AuthenticatedUser(
        String userId,
        String username,
        String employeeId,
        String displayName,
        AuthRole role,
        boolean enabled) {
}
