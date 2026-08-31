package com.fantuan.copilot.service;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.identity.VerifiedIdentity;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminAccessServiceTest {
    @Test
    void blankConfigurationDoesNotProvideAHardeningToken() {
        assertFalse(new AdminAccessService("").isAdmin(null));
        assertFalse(new AdminAccessService("  ").isAdmin("anything"));
    }

    @Test
    void configuredTokenRequiresConstantValueMatch() {
        AdminAccessService service = new AdminAccessService("test-admin");
        assertTrue(service.isAdmin("test-admin"));
        assertFalse(service.isAdmin("wrong"));
        assertFalse(service.isAdmin(null));
    }

    @Test
    void browserAdminAccessUsesVerifiedIdentityRole() {
        AdminAccessService service = new AdminAccessService("server-only-token");
        VerifiedIdentity admin = new VerifiedIdentity(
                "U90001", "admin", null, "管理员", AuthRole.ADMIN, true,
                VerifiedIdentity.Source.JWT);
        VerifiedIdentity employee = new VerifiedIdentity(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
                VerifiedIdentity.Source.JWT);

        assertTrue(service.isAdminIdentity(admin));
        assertFalse(service.isAdminIdentity(employee));
        assertFalse(service.isAdminIdentity(null));
    }

    @Test
    void disabledVerifiedAdminIsNotAuthorized() {
        VerifiedIdentity disabledAdmin = new VerifiedIdentity(
                "U90001", "admin", null, "管理员", AuthRole.ADMIN, false,
                VerifiedIdentity.Source.JWT);

        assertFalse(new AdminAccessService("server-only-token").isAdminIdentity(disabledAdmin));
    }
}
