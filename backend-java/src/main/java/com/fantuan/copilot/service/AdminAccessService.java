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
     * 校验可选的服务端 hardening 凭据。启用 hardening gate 时，空配置不是有效
     * token；调用方可以明确关闭该 gate，改用身份策略。
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
     * 面向浏览器的管理能力根据已验证 JWT 身份授权。role 由 Java 的 JWT
     * converter 重新构建，不从请求参数、localStorage 或客户端提供的 role 读取。
     */
    public boolean isAdminIdentity(VerifiedIdentity identity) {
        return identity != null && identity.enabled() && identity.role() == AuthRole.ADMIN;
    }
}
