package com.fantuan.copilot.service.memory;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Scoped Conversation Memory / Task Continuity P0 —— 状态机白名单测试。
 *
 * 合法转换（与 docs/memory-architecture.md 第 3 节状态机白名单一致）：
 *   - (None, UPSERT-ACTIVE)         首条创建
 *   - (ACTIVE, UPSERT-ACTIVE)       续写
 *   - (ACTIVE, UPSERT-COMPLETED)    终结（COMPLETE 语义）
 *   - (ACTIVE, UPSERT-ABANDONED)    终结（ABANDON 语义）
 *   - (COMPLETED, COMPLETE)         幂等重放
 *   - (ABANDONED, ABANDON)          幂等重放
 *
 * 非法转换（必须抛 409 MEMORY_STATE_CONFLICT，且不落库）：
 *   - (None, COMPLETE) / (None, ABANDON)：无记录不允许直接写终态
 *   - (COMPLETED, UPSERT-ACTIVE)：终态不可重新激活
 *   - (ABANDONED, UPSERT-ACTIVE)：终态不可重新激活
 *   - (COMPLETED, ABANDON) / (ABANDONED, COMPLETE)：终态互斥
 */
@SpringBootTest(properties = {
        "demo.identity.enabled=true",
        "business.actions.enabled=true"
})
class AiTaskMemoryStateMachineIntegrationTest extends PostgresIntegrationTestBase {

    private static final String U1 = "U10001";
    private static final String CONV_A = "11111111-1111-1111-1111-111111111111";

    @Autowired AiTaskMemoryService service;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        jdbc.execute("DELETE FROM ai_task_memory");
    }

    private void assertState(TaskStatus expected) {
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals(expected, row.status());
    }

    // ---- 合法转换 ----

    @Test
    void noneToActiveCreatesFirstRecord() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":1}", "first");
        assertState(TaskStatus.ACTIVE);
    }

    @Test
    void activeToActiveRewritesContent() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":1}", "first");
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":2}", "second");
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"step\":2}", row.taskStateJson());
        assertEquals("second", row.summary());
    }

    @Test
    void activeToCompletedTerminatesTask() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.COMPLETED, "{\"done\":true}", "done");
        assertState(TaskStatus.COMPLETED);
    }

    @Test
    void activeToAbandonedTerminatesTask() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ABANDONED, "{}", "abandoned");
        assertState(TaskStatus.ABANDONED);
    }

    @Test
    void completedToCompletedIsIdempotentReplay() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.complete(U1, CONV_A);
        assertTrue(service.complete(U1, CONV_A), "COMPLETED → COMPLETE 幂等重放应成功");
        assertState(TaskStatus.COMPLETED);
    }

    @Test
    void abandonedToAbandonedIsIdempotentReplay() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.abandon(U1, CONV_A);
        assertTrue(service.abandon(U1, CONV_A), "ABANDONED → ABANDON 幂等重放应成功");
        assertState(TaskStatus.ABANDONED);
    }

    // ---- 非法转换：必须拒绝且不落库 ----

    @Test
    void noneToCompletedIsRejected() {
        MemoryWriteException ex = assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.COMPLETED, "{}", "done"));
        assertEquals("MEMORY_STATE_CONFLICT", ex.errorCode());
        assertTrue(service.find(U1, CONV_A).isEmpty(), "被拒绝的写入不应落库");
    }

    @Test
    void noneToAbandonedIsRejected() {
        assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ABANDONED, "{}", "gone"));
        assertTrue(service.find(U1, CONV_A).isEmpty(), "被拒绝的写入不应落库");
    }

    @Test
    void completedCannotBeReactivatedByUpsert() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.complete(U1, CONV_A);
        assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"revive\":1}", "revived"));
        assertState(TaskStatus.COMPLETED);
        assertEquals("active", service.find(U1, CONV_A).orElseThrow().summary(),
                "被拒绝的覆盖不应修改原内容");
    }

    @Test
    void abandonedCannotBeReactivatedByUpsert() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.abandon(U1, CONV_A);
        assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"revive\":1}", "revived"));
        assertState(TaskStatus.ABANDONED);
    }

    @Test
    void completedCannotBeAbandoned() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.complete(U1, CONV_A);
        assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ABANDONED, "{}", "abandoned"));
        assertState(TaskStatus.COMPLETED);
    }

    @Test
    void abandonedCannotBeCompleted() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.abandon(U1, CONV_A);
        assertThrows(MemoryWriteException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.COMPLETED, "{}", "done"));
        assertState(TaskStatus.ABANDONED);
    }

    // ---- 收口辅助方法（Java 生命周期收口路径） ----

    @Test
    void completeOnMissingRecordReturnsFalseWithoutError() {
        assertFalse(service.complete(U1, CONV_A), "无记录收口应返回 false（幂等无害）");
        assertFalse(service.abandon(U1, CONV_A), "无记录收口应返回 false（幂等无害）");
    }

    @Test
    void completeAfterAbandonReturnsFalse() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.abandon(U1, CONV_A);
        assertFalse(service.complete(U1, CONV_A), "ABANDONED 后 COMPLETE 不应生效");
        assertState(TaskStatus.ABANDONED);
    }

    @Test
    void terminalTransitionPreservesTaskContent() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":3}", "keep me");
        service.complete(U1, CONV_A);
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"step\":3}", row.taskStateJson(), "收口只改状态，保留任务内容");
        assertEquals("keep me", row.summary());
    }

    // ---- 内容脱敏（Java 独立内容安全边界） ----

    @Test
    void writeFromCommandRedactsSensitiveStringValues() {
        AiTaskMemory saved = service.writeFromCommand(U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE",
                new java.util.LinkedHashMap<>(java.util.Map.of(
                        "note", "please use Bearer abc.def to call api")), "ok");
        assertEquals("{\"note\":\"[REDACTED]\"}", saved.taskStateJson());
    }

    @Test
    void writeFromCommandRedactsLongSensitiveStringValues() {
        String padded = "A".repeat(5000) + " Bearer secret-token";
        AiTaskMemory saved = service.writeFromCommand(U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE",
                new java.util.LinkedHashMap<>(java.util.Map.of("note", padded)), "");
        assertEquals("{\"note\":\"[REDACTED]\"}", saved.taskStateJson(),
                "超长字符串同样必须被扫描脱敏（不允许长度绕过）");
    }

    @Test
    void jsonStringUpsertRejectsSensitiveContent() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE,
                        "{\"note\": \"Bearer abc\"}", ""));
        assertTrue(ex.getMessage().contains("敏感内容"), ex.getMessage());
        assertTrue(service.find(U1, CONV_A).isEmpty(), "被拒绝的写入不应落库");
    }

    @Test
    void safeContentPassesThroughUnchanged() {
        java.util.LinkedHashMap<String, Object> state = new java.util.LinkedHashMap<>();
        state.put("waiting_for", "date");
        state.put("note", "normal note");
        service.writeFromCommand(U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE", state, "safe");
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"waiting_for\":\"date\",\"note\":\"normal note\"}", row.taskStateJson());
    }

    // ---- 嵌套生命周期字段剥离（上下文污染防御） ----

    @Test
    void writeFromCommandStripsNestedLifecycleFields() {
        java.util.LinkedHashMap<String, Object> state = new java.util.LinkedHashMap<>();
        state.put("pending_step", "confirmation");
        state.put("status", "COMPLETED");  // 顶层生命周期字段
        java.util.LinkedHashMap<String, Object> nested = new java.util.LinkedHashMap<>();
        nested.put("status", "COMPLETED");
        nested.put("lifecycle_state", "ABANDONED");
        nested.put("kept", 1);
        state.put("nested", nested);
        java.util.ArrayList<Object> items = new java.util.ArrayList<>();
        java.util.LinkedHashMap<String, Object> item = new java.util.LinkedHashMap<>();
        item.put("task_status", "done");
        item.put("value", "a");
        items.add(item);
        state.put("items", items);

        AiTaskMemory saved = service.writeFromCommand(
                U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE", state, "ok");
        // 生命周期字段（顶层 / 嵌套 / list 内）一律剥离，业务字段保留
        assertFalse(saved.taskStateJson().contains("COMPLETED"), saved.taskStateJson());
        assertFalse(saved.taskStateJson().contains("ABANDONED"), saved.taskStateJson());
        assertFalse(saved.taskStateJson().contains("lifecycle_state"), saved.taskStateJson());
        assertFalse(saved.taskStateJson().contains("task_status"), saved.taskStateJson());
        assertTrue(saved.taskStateJson().contains("\"pending_step\":\"confirmation\""), saved.taskStateJson());
        assertTrue(saved.taskStateJson().contains("\"kept\":1"), saved.taskStateJson());
        assertTrue(saved.taskStateJson().contains("\"value\":\"a\""), saved.taskStateJson());
        // 顶层 Memory 状态仍由 Java 控制：写入保持 ACTIVE
        assertEquals(TaskStatus.ACTIVE, saved.status());
    }

    @Test
    void writeFromCommandStripsCamelCaseLifecycleFields() {
        java.util.LinkedHashMap<String, Object> state = new java.util.LinkedHashMap<>();
        state.put("lifecycleState", "ABANDONED");
        state.put("taskStatus", "COMPLETED");
        state.put("terminalState", "finished");
        state.put("completed", true);
        state.put("abandoned", false);
        state.put("leave_date", "2026-09-01");
        AiTaskMemory saved = service.writeFromCommand(
                U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE", state, "ok");
        assertEquals("{\"leave_date\":\"2026-09-01\"}", saved.taskStateJson());
    }

    // ---- 终态不再被 Read Path 注入（与只读 ACTIVE 的 invariant 联动验证） ----

    @Test
    void findAfterTerminalTransitionStillReadsRowButStatusIsTerminal() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "active");
        service.complete(U1, CONV_A);
        Optional<AiTaskMemory> row = service.find(U1, CONV_A);
        assertTrue(row.isPresent(), "终态记录仍存在（作为历史）");
        assertEquals(TaskStatus.COMPLETED, row.orElseThrow().status());
    }
}
