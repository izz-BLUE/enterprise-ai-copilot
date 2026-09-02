package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.HitlReconciliationStatus;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;

import java.math.BigDecimal;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "demo.auth.public-password=public-test-password",
        "demo.auth.interview-password=interview-test-password",
        "demo.auth.admin-password=admin-test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class BusinessActionPersistenceIntegrationTest extends PostgresIntegrationTestBase {
    private static final VerifiedIdentity USER_A = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
    private static final VerifiedIdentity USER_B = new VerifiedIdentity(
            "U10002", "lisi", "E10001", "李四",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);

    @Autowired BusinessActionService actionService;
    TestActionService service;
    @Autowired PendingActionRepository actions;
    @Autowired LeaveAccountRepository accounts;
    @Autowired LeaveRequestRepository requests;
    @Autowired BusinessActionProperties properties;
    @Autowired AiTaskMemoryService memoryService;
    @Autowired JdbcTemplate jdbc;
    @Autowired TransactionTemplate transactionTemplate;
    @Autowired DataSource dataSource;

    private static final String CONV_LIFECYCLE = "conv-lifecycle-test";

    @BeforeEach
    void resetDatabase() {
        service = new TestActionService(actionService);
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'E10001'");
        properties.setEnabled(true);
        properties.setRequireAdmin(false);
        properties.setMaxPending(100);
    }

    @AfterEach
    void removeFailureTrigger() {
        jdbc.execute("DROP TRIGGER IF EXISTS fail_action_success ON business_action");
        jdbc.execute("DROP FUNCTION IF EXISTS reject_action_success()" );
    }

    @Test
    void flywaySchemaAndPendingDigestArePersisted() {
        Integer migrations = jdbc.queryForObject(
                "SELECT COUNT(*) FROM flyway_schema_history WHERE success", Integer.class);
        // P2-A V6: action_payload_json 泛化迁移；V7: expense_claim / expense_item；
        // P3-4 V8：持久化 HITL wait correlation；P3-5B1 V9：OA 关联列/索引；
        // P3-5B2b V10：外部审批最近检查时间戳；
        // P3-5B3 V11：外部恢复投递标记；Phase 2 V12：Java Task Runtime；
        // Expired HITL continuation delivery V13。
        assertEquals(13, migrations);
        Integer formalDomainTables = jdbc.queryForObject(
                "SELECT COUNT(*) FROM information_schema.tables "
                        + "WHERE table_schema = 'public' AND table_name IN "
                        + "('business_action', 'leave_request', 'expense_claim', 'expense_item', 'task_execution')",
                Integer.class);
        assertEquals(5, formalDomainTables);
        Integer purchaseTables = jdbc.queryForObject(
                "SELECT COUNT(*) FROM information_schema.tables "
                        + "WHERE table_schema = 'public' AND table_name = 'purchase_request'",
                Integer.class);
        assertEquals(0, purchaseTables);
        Integer v13Columns = jdbc.queryForObject(
                "SELECT COUNT(*) FROM information_schema.columns "
                        + "WHERE table_schema = 'public' AND table_name = 'business_action' "
                        + "AND column_name = 'hitl_reconciliation_status'",
                Integer.class);
        assertEquals(1, v13Columns);
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(pending.actionId()).orElseThrow().status());
        Integer digestLength = jdbc.queryForObject(
                "SELECT octet_length(confirmation_nonce_digest) FROM business_action WHERE action_id = ?",
                Integer.class, pending.actionId());
        assertEquals(32, digestLength);
        Integer plaintextColumns = jdbc.queryForObject("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'business_action' AND column_name = 'confirmation_nonce'
                """, Integer.class);
        assertEquals(0, plaintextColumns);
    }

    @Test
    void hitlWaitRegistrationIsUniqueAndRotatesOnlyThePendingNonce() {
        String executionId = "ex_" + "a".repeat(32);
        String waitId = "wait_" + "b".repeat(64);
        AnnualLeaveActionProposal proposal = proposal(nextWeekday(2));

        PendingActionView first = actionService.createHitlPending(
                proposal, "hitl-origin", null, USER_A, CONV_LIFECYCLE,
                executionId, waitId);
        PendingAction stored = actions.findByHitlWaitId(waitId).orElseThrow();
        assertEquals(first.actionId(), stored.actionId());
        assertEquals(executionId, stored.agentExecutionId());
        assertEquals(waitId, stored.hitlWaitId());

        PendingActionView retry = actionService.createHitlPending(
                proposal, "hitl-retry", null, USER_A, CONV_LIFECYCLE,
                executionId, waitId);
        assertEquals(first.actionId(), retry.actionId());
        assertNotEquals(first.confirmationNonce(), retry.confirmationNonce());
        assertEquals(1, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE hitl_wait_id = ?",
                Integer.class, waitId));

        ActionException staleNonce = assertThrows(ActionException.class,
                () -> actionService.confirm(first.actionId(), first.confirmationNonce(),
                        UUID.randomUUID().toString(), null, "old-nonce", USER_A));
        assertEquals("INVALID_CONFIRMATION_NONCE", staleNonce.errorCode());
    }

    @Test
    void terminalHitlRegistrationReconcilesAfterCapabilityRevocationWithoutNonce() {
        String executionId = "ex_" + "c".repeat(32);
        String waitId = "wait_" + "d".repeat(64);
        AnnualLeaveActionProposal proposal = proposal(nextWeekday(2));

        PendingActionView first = actionService.createHitlPending(
                proposal, "hitl-terminal", null, USER_A, CONV_LIFECYCLE,
                executionId, waitId);
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "ANNUAL_LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{}", "HITL approval in progress");
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "hitl-confirm", USER_A);

        properties.setEnabled(false);
        PendingActionView replay = actionService.createHitlPending(
                proposal, "hitl-terminal-retry", null, USER_A, CONV_LIFECYCLE,
                executionId, waitId);

        assertEquals(ActionStatus.SUCCEEDED, replay.status());
        assertNull(replay.confirmationNonce());
        assertFalse(replay.confirmationRequired());
        assertEquals(1, requests.countBySourceActionId(first.actionId()));
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void pendingHitlConfirmStillRequiresCurrentBusinessCapability() {
        PendingActionView pending = actionService.createHitlPending(
                proposal(nextWeekday(2)), "hitl-pending-capability", null, USER_A,
                CONV_LIFECYCLE, "ex_" + "e".repeat(32), "wait_" + "f".repeat(64));
        properties.setEnabled(false);

        ActionException exception = assertThrows(ActionException.class, () -> actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "revoked-confirm", USER_A));

        assertEquals("BUSINESS_ACTIONS_DISABLED", exception.errorCode());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(0, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void flywayRejectsChecksumMismatch() throws Exception {
        Path directory = Files.createTempDirectory("flyway-checksum-audit-");
        Path migration = directory.resolve("V1__create_business_action_persistence.sql");
        try {
            String original = Files.readString(Path.of("src/main/resources/db/migration/"
                    + "V1__create_business_action_persistence.sql"));
            Files.writeString(migration, original + System.lineSeparator() + "-- checksum audit");
            var result = Flyway.configure().dataSource(dataSource)
                    .locations("filesystem:" + directory.toAbsolutePath())
                    .cleanDisabled(true).load().validateWithResult();
            assertFalse(result.validationSuccessful);
        } finally {
            Files.deleteIfExists(migration);
            Files.deleteIfExists(directory);
        }
    }

    @Test
    void confirmPersistsAndReplaysSameResultForAnyValidKey() {
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        ActionExecutionResponse first = service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm-1");
        ActionExecutionResponse same = service.confirm(pending.actionId(), pending.confirmationNonce(),
                actions.find(pending.actionId()).orElseThrow().idempotencyKey().toString(), null, "confirm-2");
        ActionExecutionResponse different = service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm-3");
        assertFalse(first.replayed());
        assertTrue(same.replayed());
        assertTrue(different.replayed());
        assertEquals(first.requestId(), same.requestId());
        assertEquals(first.requestId(), different.requestId());
        assertEquals(1, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void cancelAndExpiryAreDurableStates() {
        PendingActionView cancelled = service.createPending(proposal(nextWeekday(2)), "origin", null);
        assertFalse(service.cancel(cancelled.actionId(), cancelled.confirmationNonce(), null, "c1").replayed());
        assertTrue(service.cancel(cancelled.actionId(), cancelled.confirmationNonce(), null, "c2").replayed());

        PendingActionView expired = service.createPending(proposal(nextWeekday(3)), "origin", null);
        jdbc.update("UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                + "WHERE action_id = ?", expired.actionId());
        ActionException exception = assertThrows(ActionException.class, () -> service.confirm(
                expired.actionId(), expired.confirmationNonce(), UUID.randomUUID().toString(), null, "e1"));
        assertEquals("ACTION_EXPIRED", exception.errorCode());
        assertEquals(ActionStatus.EXPIRED, actions.find(expired.actionId()).orElseThrow().status());
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void invalidCredentialsAndProcessingStateDoNotExecute() {
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        assertEquals("INVALID_CONFIRMATION_NONCE", assertThrows(ActionException.class,
                () -> service.confirm(pending.actionId(), "wrong",
                        UUID.randomUUID().toString(), null, "bad-nonce")).errorCode());
        assertEquals("INVALID_IDEMPOTENCY_KEY", assertThrows(ActionException.class,
                () -> service.confirm(pending.actionId(), pending.confirmationNonce(),
                        "not-a-uuid", null, "bad-key")).errorCode());

        jdbc.update("UPDATE business_action SET status = 'PROCESSING' WHERE action_id = ?",
                pending.actionId());
        assertEquals("ACTION_IN_PROGRESS", assertThrows(ActionException.class,
                () -> service.confirm(pending.actionId(), pending.confirmationNonce(),
                        UUID.randomUUID().toString(), null, "processing")).errorCode());
        assertEquals(0, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void concurrentConfirmCreatesOneRequestAndDeductsOnce() throws Exception {
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        List<ConnectionResult> results = confirmTogether(pending, pending);
        assertNotEquals(results.get(0).backendPid(), results.get(1).backendPid());
        assertEquals(2, results.stream().map(ConnectionResult::result)
                .filter(ActionExecutionResponse.class::isInstance).count());
        assertEquals(1, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
        assertEquals(ActionStatus.SUCCEEDED,
                actions.find(pending.actionId()).orElseThrow().status());
    }

    @Test
    void concurrentOverlappingActionsAllowAtMostOneSuccess() throws Exception {
        LocalDate date = nextWeekday(2);
        PendingActionView first = service.createPending(proposal(date), "o1", null);
        PendingActionView second = service.createPending(proposal(date), "o2", null);
        List<ConnectionResult> connectionResults = confirmTogether(first, second);
        assertNotEquals(connectionResults.get(0).backendPid(), connectionResults.get(1).backendPid());
        List<Object> results = connectionResults.stream().map(ConnectionResult::result).toList();
        assertEquals(1, results.stream().filter(ActionExecutionResponse.class::isInstance).count());
        assertEquals(1, results.stream().filter("ACTION_STALE"::equals).count());
        assertEquals(1, requests.size());
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void actionStaleIsPersistedAfterException() {
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null);
        PendingActionView stale = service.createPending(proposal(nextWeekday(3)), "stale", null);
        service.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "first-confirm");

        ActionException exception = assertThrows(ActionException.class, () -> service.confirm(
                stale.actionId(), stale.confirmationNonce(), UUID.randomUUID().toString(),
                null, "stale-confirm"));
        assertEquals("ACTION_STALE", exception.errorCode());

        var row = jdbc.queryForMap("SELECT status, failure_code, completed_at, request_id "
                + "FROM business_action WHERE action_id = ?", stale.actionId());
        assertEquals("FAILED", row.get("status"));
        assertEquals("ACTION_STALE", row.get("failure_code"));
        assertNotNull(row.get("completed_at"));
        assertNull(row.get("request_id"));
        assertEquals(1, requests.size());
        assertEquals(0, requests.countBySourceActionId(stale.actionId()));
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void infrastructureFailureRollsBackEntireConfirmation() {
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        jdbc.execute("CREATE FUNCTION reject_action_success() RETURNS trigger LANGUAGE plpgsql "
                + "AS 'BEGIN IF NEW.status = ''SUCCEEDED'' THEN "
                + "RAISE EXCEPTION ''forced test failure''; END IF; RETURN NEW; END'");
        jdbc.execute("CREATE TRIGGER fail_action_success BEFORE UPDATE ON business_action "
                + "FOR EACH ROW EXECUTE FUNCTION reject_action_success()" );
        assertThrows(RuntimeException.class, () -> service.confirm(pending.actionId(),
                pending.confirmationNonce(), UUID.randomUUID().toString(), null, "rollback"));

        var row = jdbc.queryForMap("SELECT status, request_id, idempotency_key FROM business_action "
                + "WHERE action_id = ?", pending.actionId());
        assertEquals("PENDING_CONFIRMATION", row.get("status"));
        assertNull(row.get("request_id"));
        assertNull(row.get("idempotency_key"));
        assertEquals(0, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void concurrentCreationCannotExceedPendingCapacity() throws Exception {
        properties.setMaxPending(1);
        List<Object> results = runTogether(
                () -> createResult(nextWeekday(2)), () -> createResult(nextWeekday(3)));
        assertEquals(1, results.stream().filter(PendingActionView.class::isInstance).count());
        assertEquals(1, results.stream().filter(value -> "ACTION_CAPACITY_EXCEEDED".equals(value)).count());
        assertEquals(1, actions.countActive());
    }

    // ---- 同会话活动 Action 唯一性：ai_task_memory 单条记录与多活动 Action 互斥 ----

    @Test
    void sameConversationRejectsSecondActiveAction() {
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        ActionException exception = assertThrows(ActionException.class,
                () -> service.createPending(proposal(nextWeekday(3)), "second", null, CONV_LIFECYCLE));
        assertEquals("ACTION_CONVERSATION_IN_PROGRESS", exception.errorCode());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(first.actionId()).orElseThrow().status());
        assertEquals(1, actions.countActive());
    }

    @Test
    void rejectedDuplicateKeepsExistingMemoryActiveAndConfirmable() {
        // 第一个动作的任务记忆为 ACTIVE
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "first task");
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        // 同会话第二次创建被拒（Controller 侧不得收口既有 Memory）
        ActionException exception = assertThrows(ActionException.class,
                () -> service.createPending(proposal(nextWeekday(3)), "second", null, CONV_LIFECYCLE));
        assertEquals("ACTION_CONVERSATION_IN_PROGRESS", exception.errorCode());
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status(),
                "重复请求被拒后既有动作的 Memory 必须保持 ACTIVE");
        // 第一个动作仍可正常确认，确认后 Memory 收口为 COMPLETED
        service.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "first-confirm");
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
    }

    @Test
    void cancelledActionFreesSameConversationSlot() {
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        service.cancel(first.actionId(), first.confirmationNonce(), null, "cancel");
        PendingActionView second = service.createPending(proposal(nextWeekday(3)), "second", null,
                CONV_LIFECYCLE);
        assertNotNull(second.confirmationNonce());
    }

    @Test
    void expiredActionFreesSameConversationSlot() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        jdbc.update("UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                + "WHERE action_id = ?", first.actionId());
        // createPending 内先批量过期，随后同会话检查应通过
        PendingActionView second = service.createPending(proposal(nextWeekday(3)), "second", null,
                CONV_LIFECYCLE);
        assertNotNull(second.confirmationNonce());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void chatExpiryReconciliationCommitsTerminalStateAndAllowsNewHitlAction() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "HITL approval in progress");
        PendingActionView expiredView = actionService.createHitlPending(
                proposal(nextWeekday(2)), "chat-expired", null, USER_A, CONV_LIFECYCLE,
                "ex_" + "a".repeat(32), "wait_" + "b".repeat(64));
        jdbc.update("UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                + "WHERE action_id = ?", expiredView.actionId());

        PendingAction expired = actionService.reconcileExpiredForChat(
                USER_A.userId(), CONV_LIFECYCLE, "chat-reconcile").orElseThrow();
        assertEquals(ActionStatus.EXPIRED, expired.status());
        assertEquals(ActionStatus.EXPIRED, actions.find(expiredView.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());

        PendingAction expiredRecord = actions.find(expiredView.actionId()).orElseThrow();
        assertEquals(HitlReconciliationStatus.PENDING_RECONCILIATION,
                expiredRecord.hitlReconciliationStatus());
        assertTrue(actions.markHitlReconciliationReconciled(expiredView.actionId()));
        assertEquals(HitlReconciliationStatus.RECONCILED,
                actions.find(expiredView.actionId()).orElseThrow().hitlReconciliationStatus());
        Instant completedAt = expiredRecord.completedAt();
        assertTrue(actionService.reconcileExpiredForChat(
                USER_A.userId(), CONV_LIFECYCLE, "chat-reconcile-retry").isEmpty(),
                "已成功收口的 expired HITL 不应再次被选择做 continuation");
        assertEquals(completedAt, actions.find(expiredView.actionId()).orElseThrow().completedAt());

        PendingActionView replacement = actionService.createHitlPending(
                proposal(nextWeekday(3)), "chat-new", null, USER_A, CONV_LIFECYCLE,
                "ex_" + "c".repeat(32), "wait_" + "d".repeat(64));
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(replacement.actionId()).orElseThrow().status());
    }

    @Test
    void chatExpiryReconciliationLeavesUnexpiredActionAndOtherScopesUntouched() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "HITL approval in progress");
        PendingActionView userA = actionService.createHitlPending(
                proposal(nextWeekday(2)), "chat-unexpired", null, USER_A, CONV_LIFECYCLE,
                "ex_" + "e".repeat(32), "wait_" + "f".repeat(64));
        assertTrue(actionService.reconcileExpiredForChat(
                USER_A.userId(), CONV_LIFECYCLE, "chat-unexpired-check").isEmpty());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(userA.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());

        PendingActionView userB = actionService.createHitlPending(
                proposal(nextWeekday(3)), "chat-other-user", null, USER_B, CONV_LIFECYCLE,
                "ex_" + "g".repeat(32), "wait_" + "h".repeat(64));
        assertTrue(actionService.reconcileExpiredForChat(
                USER_B.userId(), CONV_LIFECYCLE, "chat-other-user-check").isEmpty());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(userA.actionId()).orElseThrow().status());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(userB.actionId()).orElseThrow().status());
        assertTrue(actionService.reconcileExpiredForChat(
                USER_A.userId(), "conversation-without-action", "chat-no-action").isEmpty());
    }

    @Test
    void sameConversationGuardIsScopedPerConversation() {
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        // 不同 conversation 不受影响
        PendingActionView other = service.createPending(proposal(nextWeekday(3)), "other", null,
                "conv-lifecycle-other");
        assertNotNull(other.confirmationNonce());
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(first.actionId()).orElseThrow().status());
    }

    @Test
    void concurrentCreationSameConversationAllowsAtMostOne() throws Exception {
        List<Object> results = runTogether(
                () -> createResultWithConversation(nextWeekday(2), CONV_LIFECYCLE),
                () -> createResultWithConversation(nextWeekday(3), CONV_LIFECYCLE));
        assertEquals(1, results.stream().filter(PendingActionView.class::isInstance).count());
        assertEquals(1, results.stream()
                .filter(value -> "ACTION_CONVERSATION_IN_PROGRESS".equals(value)).count());
        assertEquals(1, actions.countActive());
    }

    // ---- Memory 生命周期收口：PendingAction 终态驱动 ACTIVE Memory 终结 ----

    @Test
    void confirmClosesActiveMemoryAsCompleted() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null,
                CONV_LIFECYCLE);
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm");
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void confirmReplayKeepsMemoryCompleted() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null,
                CONV_LIFECYCLE);
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "first");
        // 幂等重放确认：Memory 保持 COMPLETED（COMPLETED → COMPLETE 白名单幂等）
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                actions.find(pending.actionId()).orElseThrow().idempotencyKey().toString(),
                null, "replay");
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void cancelClosesActiveMemoryAsAbandoned() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null,
                CONV_LIFECYCLE);
        service.cancel(pending.actionId(), pending.confirmationNonce(), null, "cancel");
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void expiryClosesActiveMemoryAsAbandoned() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null,
                CONV_LIFECYCLE);
        jdbc.update("UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                + "WHERE action_id = ?", pending.actionId());
        assertThrows(ActionException.class, () -> service.confirm(pending.actionId(),
                pending.confirmationNonce(), UUID.randomUUID().toString(), null, "expired"));
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    @Test
    void batchExpiryDuringCreateClosesMemories() {
        // Python 先写 Memory，Java 后创建 PendingAction（生产顺序）
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "first task");
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "origin", null,
                CONV_LIFECYCLE);
        jdbc.update("UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                + "WHERE action_id = ?", first.actionId());

        memoryService.upsert(USER_A.userId(), "conv-lifecycle-b", "GENERIC", TaskStatus.ACTIVE,
                "{}", "second task");
        service.createPending(proposal(nextWeekday(3)), "origin", null, "conv-lifecycle-b");

        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status(),
                "批量过期应同步收口关联 Memory");
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER_A.userId(), "conv-lifecycle-b").orElseThrow().status(),
                "未过期的动作 Memory 不受影响");
    }

    @Test
    void staleFailureClosesActiveMemoryAsAbandoned() {
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "task in progress");
        PendingActionView first = service.createPending(proposal(nextWeekday(2)), "first", null,
                CONV_LIFECYCLE);
        // 两个 action 都在余额 5.0 时创建（创建不扣余额）；first 确认后余额变为 4.0，
        // stale 的 balanceBefore(5.0) 与当前余额不一致 → ACTION_STALE
        memoryService.upsert(USER_A.userId(), "conv-lifecycle-stale", "GENERIC", TaskStatus.ACTIVE,
                "{}", "stale task");
        PendingActionView stale = service.createPending(proposal(nextWeekday(3)), "stale", null,
                "conv-lifecycle-stale");
        service.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "first-confirm");

        ActionException exception = assertThrows(ActionException.class, () -> service.confirm(
                stale.actionId(), stale.confirmationNonce(), UUID.randomUUID().toString(),
                null, "stale-confirm"));
        assertEquals("ACTION_STALE", exception.errorCode());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER_A.userId(), "conv-lifecycle-stale").orElseThrow().status());
    }

    @Test
    void actionWithoutMemoryLinkSkipsClosure() {
        // conversationId=null 的历史路径：收口无副作用跳过，Memory 保持 ACTIVE
        memoryService.upsert(USER_A.userId(), CONV_LIFECYCLE, "GENERIC", TaskStatus.ACTIVE,
                "{}", "unlinked");
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm");
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER_A.userId(), CONV_LIFECYCLE).orElseThrow().status());
    }

    private Object confirmResult(PendingActionView pending) {
        try {
            return service.confirm(pending.actionId(), pending.confirmationNonce(),
                    UUID.randomUUID().toString(), null, UUID.randomUUID().toString());
        } catch (ActionException exception) {
            return exception.errorCode();
        }
    }

    private Object createResult(LocalDate date) {
        try {
            return service.createPending(proposal(date), UUID.randomUUID().toString(), null);
        } catch (ActionException exception) {
            return exception.errorCode();
        }
    }

    private Object createResultWithConversation(LocalDate date, String conversationId) {
        try {
            return service.createPending(proposal(date), UUID.randomUUID().toString(), null,
                    conversationId);
        } catch (ActionException exception) {
            return exception.errorCode();
        }
    }

    private List<Object> runTogether(java.util.concurrent.Callable<Object> first,
                                     java.util.concurrent.Callable<Object> second) throws Exception {
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Object> one = executor.submit(() -> { ready.countDown(); start.await(); return first.call(); });
            Future<Object> two = executor.submit(() -> { ready.countDown(); start.await(); return second.call(); });
            ready.await();
            start.countDown();
            return List.of(one.get(), two.get());
        } finally {
            executor.shutdownNow();
        }
    }

    private List<ConnectionResult> confirmTogether(PendingActionView first,
                                                    PendingActionView second) throws Exception {
        CyclicBarrier barrier = new CyclicBarrier(2);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<ConnectionResult> one = executor.submit(() -> confirmInTransaction(first, barrier));
            Future<ConnectionResult> two = executor.submit(() -> confirmInTransaction(second, barrier));
            return List.of(one.get(), two.get());
        } finally {
            executor.shutdownNow();
        }
    }

    private ConnectionResult confirmInTransaction(PendingActionView pending,
                                                   CyclicBarrier barrier) {
        return transactionTemplate.execute(status -> {
            Integer backendPid = jdbc.queryForObject("SELECT pg_backend_pid()", Integer.class);
            try {
                barrier.await();
            } catch (Exception exception) {
                throw new IllegalStateException("Concurrent test barrier failed", exception);
            }
            return new ConnectionResult(backendPid, confirmResult(pending));
        });
    }

    private AnnualLeaveActionProposal proposal(LocalDate date) {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                date, date, "integration test", HalfDay.NONE);
    }

    private LocalDate nextWeekday(int offset) {
        LocalDate date = service.businessDate().plusDays(offset);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return date;
    }

    private record ConnectionResult(Integer backendPid, Object result) {}

    private record TestActionService(BusinessActionService delegate) {
        PendingActionView createPending(AnnualLeaveActionProposal proposal, String traceId,
                                        String adminToken) {
            return delegate.createPending(proposal, traceId, adminToken, USER_A, null);
        }

        PendingActionView createPending(AnnualLeaveActionProposal proposal, String traceId,
                                        String adminToken, String conversationId) {
            return delegate.createPending(proposal, traceId, adminToken, USER_A, conversationId);
        }

        ActionExecutionResponse confirm(String actionId, String nonce, String idempotencyKey,
                                        String adminToken, String traceId) {
            return delegate.confirm(actionId, nonce, idempotencyKey, adminToken, traceId, USER_A);
        }

        ActionExecutionResponse cancel(String actionId, String nonce, String adminToken,
                                       String traceId) {
            return delegate.cancel(actionId, nonce, adminToken, traceId, USER_A);
        }

        LocalDate businessDate() {
            return delegate.businessDate();
        }
    }
}
