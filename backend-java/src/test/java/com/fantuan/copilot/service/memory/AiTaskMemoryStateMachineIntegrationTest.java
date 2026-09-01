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
    void expenseReasonContinuationStoresRawRequestAsCanonicalState() {
        String q1 = "根据我最近一次已批准的出差和对应发票，帮我准备差旅报销申请。";

        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, q1);

        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals("EXPENSE_REQUEST", row.taskType());
        assertEquals(TaskStatus.ACTIVE, row.status());
        assertEquals("{\"waiting_for\":\"reason\",\"original_request\":\""
                + q1 + "\"}", row.taskStateJson());
    }

    @Test
    void expenseReasonContinuationIsWriteOnceAcrossFollowUpInput() {
        String q1 = "根据最近一次已批准出差准备报销。";
        String q2 = "客户拜访";

        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, q1);
        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, q2);

        AiTaskMemory clarification = service.find(U1, CONV_A).orElseThrow();
        assertTrue(clarification.taskStateJson().contains("\"waiting_for\":\"reason\""));
        assertTrue(clarification.taskStateJson().contains("\"original_request\":\""
                + q1 + "\""));
    }

    @Test
    void ordinaryExpenseProposalPreservesOriginalRequestAfterReasonFollowUp() {
        String q1 = "根据我最近一次已批准的出差和对应发票，帮我准备差旅报销申请。";

        service.startNewActiveExpenseReasonCycle(U1, CONV_A, q1);
        service.upsertActiveFromAgent(U1, CONV_A, "EXPENSE_REQUEST",
                java.util.Map.of("waiting_for", "user_confirmation",
                        "reason", "客户拜访"),
                "已生成报销申请草稿，等待确认");

        AiTaskMemory proposal = service.find(U1, CONV_A).orElseThrow();
        assertTrue(proposal.taskStateJson().contains("\"original_request\":\""
                + q1 + "\""), "原因补充后的普通 proposal 写入不得丢失 Q1");
        assertTrue(proposal.taskStateJson().contains("\"reason\":\"客户拜访\""));
    }

    @Test
    void terminalExpenseMemoryCanStartExplicitNewReasonCycle() {
        String oldRequest = "旧的报销申请";
        String newRequest = "帮我准备差旅报销申请。";

        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, oldRequest);
        service.abandon(U1, CONV_A);
        service.startNewActiveExpenseReasonCycle(U1, CONV_A, newRequest);

        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals(TaskStatus.ACTIVE, row.status());
        assertEquals("EXPENSE_REQUEST", row.taskType());
        assertEquals("{\"waiting_for\":\"reason\",\"original_request\":\""
                + newRequest + "\"}", row.taskStateJson());
    }

    @Test
    void ordinaryExpenseContinuationStillCannotReactivateTerminalMemory() {
        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, "旧的报销申请");
        service.abandon(U1, CONV_A);

        assertThrows(MemoryWriteException.class, () ->
                service.upsertActiveExpenseReasonContinuation(U1, CONV_A, "新的报销申请"));
        assertEquals(TaskStatus.ABANDONED,
                service.find(U1, CONV_A).orElseThrow().status());
    }

    @Test
    void terminalExpenseMemoryDoesNotLeakIntoNewLeaveTask() {
        service.upsertActiveExpenseReasonContinuation(U1, CONV_A, "旧的报销申请");
        service.abandon(U1, CONV_A);

        service.upsertActiveForNextTask(U1, CONV_A, "LEAVE_REQUEST",
                java.util.Map.of("waiting_for", "date"), "等待请假日期");

        AiTaskMemory next = service.find(U1, CONV_A).orElseThrow();
        assertEquals(TaskStatus.ACTIVE, next.status());
        assertEquals("LEAVE_REQUEST", next.taskType());
        assertEquals("{\"waiting_for\":\"date\"}", next.taskStateJson());
        assertFalse(next.taskStateJson().contains("旧的报销申请"));
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
    void taskRuntimeNextTaskUsesExplicitTerminalReactivation() {
        service.upsert(U1, CONV_A, "LEAVE_REQUEST", TaskStatus.ACTIVE,
                "{\"task\":1}", "task one");
        service.complete(U1, CONV_A);

        assertThrows(MemoryWriteException.class, () ->
                service.upsertActiveFromAgent(U1, CONV_A, "EXPENSE_CLAIM",
                        java.util.Map.of("task", 2), "task two"),
                "通用 Agent proposal 仍不能重新激活终态 Memory");

        service.upsertActiveForNextTask(U1, CONV_A, "EXPENSE_CLAIM",
                java.util.Map.of("task", 2), "task two");
        AiTaskMemory next = service.find(U1, CONV_A).orElseThrow();
        assertEquals(TaskStatus.ACTIVE, next.status());
        assertEquals("EXPENSE_CLAIM", next.taskType());
        assertEquals("{\"task\":2}", next.taskStateJson());
        assertEquals("task two", next.summary());
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
    void agentProposalRedactsSensitiveStringValues() {
        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC",
                new java.util.LinkedHashMap<>(java.util.Map.of(
                        "note", "please use Bearer abc.def to call api")), "ok");
        AiTaskMemory saved = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"note\":\"[REDACTED]\"}", saved.taskStateJson());
    }

    @Test
    void agentProposalRedactsLongSensitiveStringValues() {
        String padded = "A".repeat(5000) + " Bearer secret-token";
        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC",
                new java.util.LinkedHashMap<>(java.util.Map.of("note", padded)), "");
        AiTaskMemory saved = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"note\":\"[REDACTED]\"}", saved.taskStateJson(),
                "超长字符串同样必须被扫描脱敏（不允许长度绕过）");
    }

    @Test
    void agentProposalRedactsSensitiveSummary() {
        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC", java.util.Map.of(),
                "Bearer TOP_SECRET");
        assertEquals("[REDACTED]", service.find(U1, CONV_A).orElseThrow().summary());
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
        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC", state, "safe");
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertEquals("{\"waiting_for\":\"date\",\"note\":\"normal note\"}", row.taskStateJson());
    }

    // ---- 嵌套生命周期字段剥离（上下文污染防御） ----

    @Test
    void agentProposalStripsNestedLifecycleFields() {
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

        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC", state, "ok");
        AiTaskMemory saved = service.find(U1, CONV_A).orElseThrow();
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
    void agentProposalStripsCamelCaseLifecycleFields() {
        java.util.LinkedHashMap<String, Object> state = new java.util.LinkedHashMap<>();
        state.put("lifecycleState", "ABANDONED");
        state.put("taskStatus", "COMPLETED");
        state.put("terminalState", "finished");
        state.put("completed", true);
        state.put("abandoned", false);
        state.put("leave_date", "2026-09-01");
        service.upsertActiveFromAgent(U1, CONV_A, "GENERIC", state, "ok");
        AiTaskMemory saved = service.find(U1, CONV_A).orElseThrow();
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
