package com.fantuan.copilot.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminAccessServiceTest {
    @Test
    void blankConfigurationUsesDemoMode() {
        assertTrue(new AdminAccessService("").isAdmin(null));
        assertTrue(new AdminAccessService("  ").isAdmin("anything"));
    }

    @Test
    void configuredTokenRequiresConstantValueMatch() {
        AdminAccessService service = new AdminAccessService("test-admin");
        assertTrue(service.isAdmin("test-admin"));
        assertFalse(service.isAdmin("wrong"));
        assertFalse(service.isAdmin(null));
    }
}
