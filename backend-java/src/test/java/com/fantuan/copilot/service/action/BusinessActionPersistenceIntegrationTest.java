package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
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
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class BusinessActionPersistenceIntegrationTest extends PostgresIntegrationTestBase {
    @Autowired BusinessActionService service;
    @Autowired PendingActionRepository actions;
    @Autowired LeaveAccountRepository accounts;
    @Autowired LeaveRequestRepository requests;
    @Autowired BusinessActionProperties properties;
    @Autowired JdbcTemplate jdbc;
    @Autowired TransactionTemplate transactionTemplate;
    @Autowired DataSource dataSource;

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'DEMO-001'");
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
        assertEquals(1, migrations);
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
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("DEMO-001").orElseThrow());
    }

    @Test
    void concurrentConfirmCreatesOneRequestAndDeductsOnce() throws Exception {
        PendingActionView pending = service.createPending(proposal(nextWeekday(2)), "origin", null);
        List<ConnectionResult> results = confirmTogether(pending, pending);
        assertNotEquals(results.get(0).backendPid(), results.get(1).backendPid());
        assertEquals(2, results.stream().map(ConnectionResult::result)
                .filter(ActionExecutionResponse.class::isInstance).count());
        assertEquals(1, requests.countBySourceActionId(pending.actionId()));
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
        assertEquals(new BigDecimal("4.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("DEMO-001").orElseThrow());
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
}
