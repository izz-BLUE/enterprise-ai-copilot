package com.fantuan.copilot.service.action;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;

class BusinessActionPropertiesTest {
    @Test
    void requireAdminDefaultsToFalse() {
        assertFalse(new BusinessActionProperties().isRequireAdmin());
    }
}
