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

        // 篡改语义字段（expiry 数值），确定性触发签名校验失败。
        // 注意：不能只篡改 sig 的最后一个 base64url 字符 —— 其低 2 bit 是
        // padding 冗余位，sig 以 A/B/C/D 结尾时（约 6.25% 概率）篡改后解码
        // 字节不变，验证仍通过，导致断言不稳定（历史偶发失败根因）。
        String[] parts = token.split("\\.", -1);
        long expiresAt = Long.parseLong(parts[3]) + 1;
        String tampered = String.join(".", parts[0], parts[1], parts[2],
                String.valueOf(expiresAt), parts[4], parts[5]);

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
