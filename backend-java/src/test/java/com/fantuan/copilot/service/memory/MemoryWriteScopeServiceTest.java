package com.fantuan.copilot.service.memory;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MemoryWriteScopeServiceTest {

    private static final Instant NOW = Instant.parse("2026-08-20T00:00:00Z");

    @Test
    void scopeRoundTripsOwnerAndConversation() {
        MemoryWriteScopeService service = serviceAt(NOW);
        String token = service.issue("U10001", "conversation-a");

        var scope = service.verify(token);

        assertEquals("U10001", scope.userId());
        assertEquals("conversation-a", scope.conversationId());
        assertTrue(service.matchesInternalToken("internal-token"));
    }

    @Test
    void tamperedScopeIsRejected() {
        MemoryWriteScopeService service = serviceAt(NOW);
        String token = service.issue("U10001", "conversation-a");
        String tampered = token.substring(0, token.length() - 1)
                + (token.endsWith("A") ? "B" : "A");

        assertThrows(IllegalArgumentException.class, () -> service.verify(tampered));
    }

    @Test
    void expiredScopeIsRejected() {
        MemoryWriteScopeService issued = serviceAt(NOW);
        String token = issued.issue("U10001", "conversation-a");
        MemoryWriteScopeService expired = serviceAt(NOW.plusSeconds(120));

        assertThrows(IllegalArgumentException.class, () -> expired.verify(token));
    }

    @Test
    void blankInternalTokenDisablesIssuingAndVerification() {
        MemoryWriteScopeService service = new MemoryWriteScopeService(
                "", Clock.fixed(NOW, ZoneOffset.UTC));

        assertNull(service.issue("U10001", "conversation-a"));
        assertFalse(service.matchesInternalToken("internal-token"));
        assertThrows(IllegalArgumentException.class, () -> service.verify(null));
    }

    private MemoryWriteScopeService serviceAt(Instant now) {
        return new MemoryWriteScopeService(
                "internal-token", Clock.fixed(now, ZoneOffset.UTC));
    }
}
