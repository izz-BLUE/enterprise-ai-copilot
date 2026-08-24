package com.fantuan.copilot.adminlog;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminLogBufferTest {

    private AdminLogEvent sample(String level, String category, String event, String traceId, Instant ts) {
        return new AdminLogEvent(
                "id-" + ts.toEpochMilli(),
                ts,
                level,
                category,
                event,
                traceId,
                "backend-java",
                "userRef",
                "actionRef",
                "FROM",
                "TO",
                1L,
                "msg",
                null,
                null,
                null);
    }

    @Test
    void overflowKeepsLatestFiveHundred() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        for (int i = 0; i < 600; i++) {
            buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "X", null,
                    Instant.ofEpochMilli(i)));
        }
        assertEquals(500, buffer.size());
        // 第一页：最新 100 条
        List<AdminLogEvent> firstPage = buffer.snapshot(null, null, null, 100);
        assertEquals(100, firstPage.size());
        assertEquals(Instant.ofEpochMilli(599), firstPage.get(0).timestamp());
        assertEquals(Instant.ofEpochMilli(500), firstPage.get(99).timestamp());
        // 容量在 500：size 永远是 500，不会随查询变化
        assertEquals(500, buffer.size());
        // 写入更多继续滚动到 500 上限
        for (int i = 600; i < 700; i++) {
            buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "Y", null,
                    Instant.ofEpochMilli(i)));
        }
        assertEquals(500, buffer.size());
        List<AdminLogEvent> latest = buffer.snapshot(null, null, null, 100);
        assertEquals(Instant.ofEpochMilli(699), latest.get(0).timestamp());
        assertEquals(Instant.ofEpochMilli(600), latest.get(99).timestamp());
    }

    @Test
    void filtersByLevelCategoryAndTraceId() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        Instant base = Instant.parse("2026-01-01T00:00:00Z");
        buffer.record(sample("INFO", AdminLogEvent.CATEGORY_AGENT, "AGENT_REQUEST_RECEIVED",
                "trace-a", base));
        buffer.record(sample("ERROR", AdminLogEvent.CATEGORY_MEMORY, "MEMORY_WRITE_REJECTED",
                "trace-b", base.plusSeconds(1)));
        buffer.record(sample("WARN", AdminLogEvent.CATEGORY_BUSINESS_ACTION, "ACTION_CANCELLED",
                "trace-a", base.plusSeconds(2)));

        List<AdminLogEvent> onlyError = buffer.snapshot("ERROR", null, null, null);
        assertEquals(1, onlyError.size());
        assertEquals("MEMORY_WRITE_REJECTED", onlyError.get(0).event());

        List<AdminLogEvent> onlyMemory = buffer.snapshot(null, "MEMORY", null, null);
        assertEquals(1, onlyMemory.size());
        assertEquals("ERROR", onlyMemory.get(0).level());

        List<AdminLogEvent> traceMatch = buffer.snapshot(null, null, "trace-a", null);
        assertEquals(2, traceMatch.size());

        List<AdminLogEvent> traceMiss = buffer.snapshot(null, null, "trace-c", null);
        assertTrue(traceMiss.isEmpty());
    }

    @Test
    void invalidFilterReturnsBadRequest() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "X", null, Instant.now()));
        assertThrows(IllegalArgumentException.class,
                () -> buffer.snapshot("BOGUS", null, null, null));
        assertThrows(IllegalArgumentException.class,
                () -> buffer.snapshot(null, "BOGUS", null, null));
        assertThrows(IllegalArgumentException.class,
                () -> buffer.snapshot(null, null, null, 0));
        assertThrows(IllegalArgumentException.class,
                () -> buffer.snapshot(null, null, null, 500));
    }

    @Test
    void snapshotIsReverseChronological() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        Instant t = Instant.parse("2026-02-01T00:00:00Z");
        buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "A", null, t));
        buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "B", null, t.plusSeconds(1)));
        buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "C", null, t.plusSeconds(2)));
        List<AdminLogEvent> snapshot = buffer.snapshot(null, null, null, 10);
        assertEquals("C", snapshot.get(0).event());
        assertEquals("B", snapshot.get(1).event());
        assertEquals("A", snapshot.get(2).event());
        assertFalse(snapshot.isEmpty());
    }

    @Test
    void defaultLimitIs50() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        for (int i = 0; i < 100; i++) {
            buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM, "X", null,
                    Instant.ofEpochMilli(i)));
        }
        List<AdminLogEvent> snapshot = buffer.snapshot(null, null, null, null);
        assertEquals(50, snapshot.size());
    }

    @Test
    void pageReturnsAccurateTotalAndStableOffset() {
        AdminLogBuffer buffer = new AdminLogBuffer();
        for (int i = 0; i < 7; i++) {
            buffer.record(sample("INFO", AdminLogEvent.CATEGORY_SYSTEM,
                    "E" + i, null, Instant.ofEpochMilli(i)));
        }
        AdminLogBuffer.Page page = buffer.snapshotPage(null, null, null, 3, 3);
        assertEquals(7, page.total());
        assertEquals(3, page.offset());
        assertTrue(page.hasMore());
        assertEquals(List.of("E3", "E2", "E1"),
                page.items().stream().map(AdminLogEvent::event).toList());
        assertThrows(IllegalArgumentException.class,
                () -> buffer.snapshotPage(null, null, null, 3, -1));
    }
}
