package com.fantuan.copilot.config;

import com.fantuan.copilot.auth.AuthProperties;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertThrows;

class SecurityConfigSecretValidationTest {

    @Test
    void missingSecretFailsFast() {
        AuthProperties properties = new AuthProperties();
        properties.setSecret(null);

        assertThrows(IllegalStateException.class,
                () -> new SecurityConfig().jwtEncoder(properties));
    }

    @Test
    void blankSecretFailsFast() {
        AuthProperties properties = new AuthProperties();
        properties.setSecret("   ");

        assertThrows(IllegalStateException.class,
                () -> new SecurityConfig().jwtEncoder(properties));
    }

    @Test
    void shortSecretFailsFast() {
        AuthProperties properties = new AuthProperties();
        properties.setSecret("too-short");

        assertThrows(IllegalStateException.class,
                () -> new SecurityConfig().jwtEncoder(properties));
    }
}
