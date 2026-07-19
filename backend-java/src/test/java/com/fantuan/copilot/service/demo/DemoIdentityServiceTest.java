package com.fantuan.copilot.service.demo;

import com.fantuan.copilot.service.action.ActionException;
import org.junit.jupiter.api.Test;

import java.util.HashSet;

import static org.junit.jupiter.api.Assertions.*;

class DemoIdentityServiceTest {
    @Test
    void enabledDirectoryContainsThreeUniqueImmutableIdentities() {
        DemoIdentityService service = service(true);
        var identities = service.listEnabled();

        assertEquals(3, identities.size());
        assertEquals(3, new HashSet<>(identities.stream().map(DemoIdentity::userId).toList()).size());
        assertEquals(3, new HashSet<>(identities.stream().map(DemoIdentity::employeeId).toList()).size());
        assertEquals(DemoRole.EMPLOYEE, service.requireIdentity(" DEMO-001 ").role());
        assertEquals(DemoRole.MANAGER, service.requireIdentity("DEMO-MGR-001").role());
        assertTrue(service.find("demo-001").isEmpty());
    }

    @Test
    void missingUnknownAndDisabledHaveSafeDistinctSemantics() {
        assertException(service(true), null, "DEMO_IDENTITY_REQUIRED", 400);
        assertException(service(true), "unknown-sensitive-value", "DEMO_IDENTITY_INVALID", 403);
        assertException(service(false), "DEMO-001", "DEMO_IDENTITY_DISABLED", 503);
    }

    private DemoIdentityService service(boolean enabled) {
        DemoIdentityProperties properties = new DemoIdentityProperties();
        properties.setEnabled(enabled);
        return new DemoIdentityService(properties);
    }

    private void assertException(DemoIdentityService service, String value,
                                 String code, int status) {
        ActionException exception = assertThrows(ActionException.class,
                () -> service.requireIdentity(value));
        assertEquals(code, exception.errorCode());
        assertEquals(status, exception.httpStatus().value());
        assertFalse(exception.getMessage().contains("unknown-sensitive-value"));
    }
}
