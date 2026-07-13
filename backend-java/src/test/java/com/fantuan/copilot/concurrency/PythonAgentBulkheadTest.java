package com.fantuan.copilot.concurrency;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PythonAgentBulkheadTest {

    @Test
    void rejectsWhenFullAndRecoversAfterPermitCloses() {
        PythonAgentBulkhead bulkhead = new PythonAgentBulkhead(1, 10);

        PythonAgentBulkhead.Permit first = bulkhead.tryAcquire("first");
        assertNotNull(first);
        assertEquals(1, bulkhead.snapshot().get("active"));

        assertNull(bulkhead.tryAcquire("second"));
        assertEquals(1L, bulkhead.snapshot().get("rejected"));

        first.close();
        first.close();
        assertEquals(1, bulkhead.snapshot().get("available"));
        PythonAgentBulkhead.Permit third = bulkhead.tryAcquire("third");
        assertNotNull(third);
        third.close();
        assertEquals(1, bulkhead.snapshot().get("available"));
    }

    @Test
    void validatesSettings() {
        assertThrows(IllegalArgumentException.class, () -> new PythonAgentBulkhead(0, 10));
        assertThrows(IllegalArgumentException.class, () -> new PythonAgentBulkhead(1, 0));
    }
}
