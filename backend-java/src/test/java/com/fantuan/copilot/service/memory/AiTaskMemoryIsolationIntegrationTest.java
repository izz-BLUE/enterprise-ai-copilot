package com.fantuan.copilot.service.memory;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.memory.AiTaskMemoryRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.util.Optional;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Scoped Conversation Memory / Task Continuity P0 —— 隔离测试。
 *
 * 覆盖：
 *   - U10001 + conversation A 无法读到 U10002 + conversation A
 *   - U10001 + conversation A 与 U10001 + conversation B 相互隔离
 *   - upsert 只更新准确的 (user_id, conversation_id)
 *   - delete 不能删除其他用户同 conversation_id 的记录
 *   - 边界：超长 task_state_json / summary 写入被服务层拒绝
 *   - 边界：status 为 null 时拒绝
 */
@SpringBootTest(properties = {
        "demo.identity.enabled=true",
        "business.actions.enabled=true"
})
class AiTaskMemoryIsolationIntegrationTest extends PostgresIntegrationTestBase {

    private static final String U1 = "U10001";
    private static final String U2 = "U10002";
    private static final String CONV_A = "11111111-1111-1111-1111-111111111111";
    private static final String CONV_B = "22222222-2222-2222-2222-222222222222";

    @Autowired AiTaskMemoryService service;
    @Autowired AiTaskMemoryRepository repository;
    @Autowired JdbcTemplate jdbc;
    @Autowired NamedParameterJdbcTemplate namedJdbc;

    @BeforeEach
    void reset() {
        jdbc.execute("DELETE FROM ai_task_memory");
    }

    // ---- 1. 不同 user 不会读到对方记录 ----

    @Test
    void userAConversationANotVisibleToUserBWithSameConversationId() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":1}", "U1-A");

        // U2 用相同 conversationId 来查，应当看不到。
        Optional<AiTaskMemory> u2View = service.find(U2, CONV_A);
        assertTrue(u2View.isEmpty(), "U2 不应能读到 U1 的记录（即便 conversationId 相同）");

        // U1 自己能读到。
        AiTaskMemory u1View = service.find(U1, CONV_A).orElseThrow();
        assertEquals("U1-A", u1View.summary());
        assertEquals(U1, u1View.userId());
        assertEquals(CONV_A, u1View.conversationId());
    }

    // ---- 2. 同一 user 的不同 conversation 互不干扰 ----

    @Test
    void userConversationAAndConversationBAreIsolated() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"step\":1}", "A-summary");
        // 终态必须经由合法状态转换（无记录不允许直接写 COMPLETED）
        service.upsert(U1, CONV_B, "GENERIC", TaskStatus.ACTIVE, "{\"step\":99}", "B-summary");
        service.complete(U1, CONV_B);

        AiTaskMemory a = service.find(U1, CONV_A).orElseThrow();
        AiTaskMemory b = service.find(U1, CONV_B).orElseThrow();

        assertEquals("A-summary", a.summary());
        assertEquals(TaskStatus.ACTIVE, a.status());
        assertEquals("{\"step\":1}", a.taskStateJson());

        assertEquals("B-summary", b.summary());
        assertEquals(TaskStatus.COMPLETED, b.status());
        assertEquals("{\"step\":99}", b.taskStateJson());

        // 直接走仓储再确认一次，避免 service 层 cache 之类的副作用。
        assertEquals("A-summary", repository.find(U1, CONV_A).orElseThrow().summary());
        assertEquals("B-summary", repository.find(U1, CONV_B).orElseThrow().summary());
    }

    // ---- 3. upsert 只命中准确的 (user_id, conversation_id)，不会污染他人记录 ----

    @Test
    void upsertOnlyUpdatesTheExactCompositeKeyAndDoesNotTouchOtherRows() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"v\":1}", "first");
        service.upsert(U2, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{\"v\":2}", "u2-first");

        // U1 重写 CONV_A；U2 在 CONV_A 的记录必须保持不变。
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.COMPLETED, "{\"v\":99}", "u1-second");

        AiTaskMemory u1A = service.find(U1, CONV_A).orElseThrow();
        AiTaskMemory u2A = service.find(U2, CONV_A).orElseThrow();

        assertEquals("u1-second", u1A.summary());
        assertEquals(TaskStatus.COMPLETED, u1A.status());
        assertEquals("{\"v\":99}", u1A.taskStateJson());
        assertTrue(u1A.updatedAt().isAfter(u1A.createdAt()) || u1A.updatedAt().equals(u1A.createdAt()));

        assertEquals("u2-first", u2A.summary(), "U2 的 CONV_A 不应被 U1 的 upsert 污染");
        assertEquals(TaskStatus.ACTIVE, u2A.status());

        // 数据库层行数核对：应该恰好 2 条。
        Integer count = namedJdbc.queryForObject(
                "SELECT COUNT(*) FROM ai_task_memory WHERE user_id IN (:u1, :u2)",
                new MapSqlParameterSource().addValue("u1", U1).addValue("u2", U2),
                Integer.class);
        assertEquals(2, count);
    }

    // ---- 4. delete 只能删自己 (user_id, conversation_id) 的记录 ----

    @Test
    void deleteDoesNotRemoveRowsOfOtherUsersWithSameConversationId() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "u1-A");
        service.upsert(U2, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "u2-A");

        int affected = service.delete(U1, CONV_A);
        assertEquals(1, affected, "应只删 1 条");

        assertTrue(service.find(U1, CONV_A).isEmpty(), "U1 的 CONV_A 应被删除");
        assertTrue(service.find(U2, CONV_A).isPresent(), "U2 的 CONV_A 必须仍在");

        // 重复删同一个 key，affected 必须为 0。
        int affectedAgain = service.delete(U1, CONV_A);
        assertEquals(0, affectedAgain);
    }

    // ---- 5. delete 也不会影响同一用户的其他 conversation ----

    @Test
    void deleteOnOneConversationDoesNotAffectSiblingConversation() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "A");
        service.upsert(U1, CONV_B, "GENERIC", TaskStatus.ACTIVE, "{}", "B");

        int affected = service.delete(U1, CONV_A);
        assertEquals(1, affected);

        assertTrue(service.find(U1, CONV_A).isEmpty());
        assertTrue(service.find(U1, CONV_B).isPresent(), "同用户 CONV_B 不应被波及");
    }

    // ---- 6. 边界：service 层对非法写入拒绝 ----

    @Test
    void upsertRejectsOversizedTaskStateJson() {
        String tooLarge = "x".repeat(AiTaskMemoryService.MAX_TASK_STATE_JSON_BYTES + 1);
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, tooLarge, ""));
        assertTrue(ex.getMessage().contains("task_state_json"), ex.getMessage());
        assertTrue(service.find(U1, CONV_A).isEmpty(), "失败的写入不应落库");
    }

    @Test
    void upsertRejectsOversizedSummary() {
        String tooLong = "s".repeat(AiTaskMemoryService.MAX_SUMMARY_CHARS + 1);
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", tooLong));
        assertTrue(ex.getMessage().contains("summary"), ex.getMessage());
    }

    @Test
    void upsertRejectsNullStatus() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
                service.upsert(U1, CONV_A, "GENERIC", null, "{}", ""));
        assertTrue(ex.getMessage().contains("status"), ex.getMessage());
    }

    @Test
    void upsertRejectsCamelCaseTrustedRuntimeFields() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
                service.writeFromCommand(U1, CONV_A, "UPSERT", "GENERIC", "ACTIVE",
                        new java.util.LinkedHashMap<>(Map.of(
                                "businessDate", "2026-08-20",
                                "nested", Map.of("traceId", "t1"))), ""));
        assertTrue(ex.getMessage().contains("trusted"), ex.getMessage());
        assertTrue(service.find(U1, CONV_A).isEmpty(), "失败的写入不应落库");
    }

    @Test
    void upsertRejectsBlankUserIdOrConversationId() {
        assertThrows(IllegalArgumentException.class, () ->
                service.upsert("", CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", ""));
        assertThrows(IllegalArgumentException.class, () ->
                service.upsert(U1, "", "GENERIC", TaskStatus.ACTIVE, "{}", ""));
        assertThrows(IllegalArgumentException.class, () ->
                service.upsert(null, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", ""));
    }

    @Test
    void findWithUnknownUserIdReturnsEmpty() {
        service.upsert(U1, CONV_A, "GENERIC", TaskStatus.ACTIVE, "{}", "");
        assertTrue(service.find("U-DOES-NOT-EXIST", CONV_A).isEmpty());
        assertTrue(service.find(U1, "conv-does-not-exist").isEmpty());
    }

    // ---- 7. 简便 upsert（默认值）也能写入且按复合 key 工作 ----

    @Test
    void defaultUpsertPopulatesRequiredFieldsAndHonorsCompositeKey() {
        service.upsert(U1, CONV_A);
        AiTaskMemory row = service.find(U1, CONV_A).orElseThrow();
        assertNotNull(row);
        assertEquals(TaskStatus.ACTIVE, row.status());
        assertEquals("GENERIC", row.taskType());
        assertEquals("{}", row.taskStateJson());
        assertEquals("", row.summary());
        assertNotNull(row.createdAt());
        assertNotNull(row.updatedAt());
        assertFalse(service.find(U2, CONV_A).isPresent());
    }
}
