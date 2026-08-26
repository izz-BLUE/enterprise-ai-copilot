package com.fantuan.copilot.service.agent;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AgentRuntimeThreadIdServiceTest {
    private final AgentRuntimeThreadIdService service = new AgentRuntimeThreadIdService();

    @Test
    void sameUserAndConversationProduceStableThreadId() {
        assertEquals(service.generate("U10001", "conversation-a"),
                service.generate("U10001", "conversation-a"));
    }

    @Test
    void differentUsersWithSameConversationAreIsolated() {
        assertNotEquals(service.generate("U10001", "conversation-a"),
                service.generate("U10002", "conversation-a"));
    }

    @Test
    void sameUserWithDifferentConversationsAreIsolated() {
        assertNotEquals(service.generate("U10001", "conversation-a"),
                service.generate("U10001", "conversation-b"));
    }

    @Test
    void outputUsesFixedRuntimeThreadIdFormat() {
        assertTrue(service.generate("U10001", "conversation-a").matches("^rt_[0-9a-f]{64}$"));
    }
}
