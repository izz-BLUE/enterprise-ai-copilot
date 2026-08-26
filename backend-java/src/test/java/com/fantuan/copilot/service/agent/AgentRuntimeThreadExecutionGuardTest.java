package com.fantuan.copilot.service.agent;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AgentRuntimeThreadExecutionGuardTest {

    @Test
    void sameThreadCannotBeAcquiredTwiceAndCanBeReacquiredAfterRelease() {
        AgentRuntimeThreadExecutionGuard guard = new AgentRuntimeThreadExecutionGuard();

        assertTrue(guard.tryAcquire("thread-a"));
        assertFalse(guard.tryAcquire("thread-a"));
        guard.release("thread-a");
        assertTrue(guard.tryAcquire("thread-a"));
        guard.release("thread-a");
    }

    @Test
    void differentThreadsDoNotBlockEachOther() {
        AgentRuntimeThreadExecutionGuard guard = new AgentRuntimeThreadExecutionGuard();

        assertTrue(guard.tryAcquire("thread-a"));
        assertTrue(guard.tryAcquire("thread-b"));
        guard.release("thread-a");
        guard.release("thread-b");
        guard.release("thread-b");
    }
}
