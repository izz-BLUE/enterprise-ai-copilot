package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.InMemoryPendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

class BusinessActionServiceTest {
    private static final String ADMIN = "test-admin";

    @Test
    void validProposalCreatesPendingActionAndStoresOnlyNonceDigest() {
        Fixture fixture = fixture();
        PendingActionView view = fixture.service.createPending(proposal(
                LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 24), HalfDay.NONE, " 私事 "),
                "origin", ADMIN);

        assertEquals(ActionStatus.PENDING_CONFIRMATION, view.status());
        assertEquals(new BigDecimal("5.0"), view.summary().days());
        assertEquals(new BigDecimal("5.0"), view.summary().remainingBalanceBefore());
        assertEquals(new BigDecimal("0.0"), view.summary().remainingBalanceAfter());
        assertNotNull(view.confirmationNonce());
        PendingAction stored = fixture.repository.find(view.actionId()).orElseThrow();
        assertFalse(new String(stored.confirmationNonceDigest()).contains(view.confirmationNonce()));
    }

    @Test
    void originTraceIdIsStoredAndReturnedByConfirm() {
        Fixture fixture = fixture();
        PendingActionView pending = fixture.service.createPending(
                standardProposal(), "java-trace-123", ADMIN);

        PendingAction stored = fixture.repository.find(pending.actionId()).orElseThrow();
        assertEquals("java-trace-123", stored.originTraceId());

        ActionExecutionResponse confirmed = fixture.service.confirm(
                pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), ADMIN, "confirm-trace-456");
        assertEquals("java-trace-123", confirmed.originTraceId());
        assertNotEquals("python-trace-999", confirmed.originTraceId());
    }

    @Test
    void featureFlagAndAdminAreEnforced() {
        Fixture fixture = fixture();
        fixture.properties.setEnabled(false);
        assertCode("BUSINESS_ACTIONS_DISABLED", () -> fixture.service.createPending(standardProposal(), "o", ADMIN));
        fixture.properties.setEnabled(true);
        assertCode("ADMIN_REQUIRED", () -> fixture.service.createPending(standardProposal(), "o", "wrong"));
        fixture.properties.setRequireAdmin(false);
        assertNotNull(fixture.service.createPending(standardProposal(), "o", null));
    }

    @ParameterizedTest
    @MethodSource("invalidProposals")
    void javaRevalidatesUntrustedProposal(AnnualLeaveActionProposal proposal) {
        assertCode("BUSINESS_RULE_VIOLATION",
                () -> fixture().service.createPending(proposal, "origin", ADMIN));
    }

    static Stream<Arguments> invalidProposals() {
        return Stream.of(
                Arguments.of(new AnnualLeaveActionProposal(null, LocalDate.of(2026, 7, 20),
                        LocalDate.of(2026, 7, 20), "x", HalfDay.NONE)),
                Arguments.of(proposal(LocalDate.of(2026, 7, 15), LocalDate.of(2026, 7, 16), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 21), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 8, 20), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x".repeat(201))),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x\n")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 18), LocalDate.of(2026, 7, 19), HalfDay.NONE, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 21), HalfDay.AM, "x")),
                Arguments.of(proposal(LocalDate.of(2026, 7, 18), LocalDate.of(2026, 7, 18), HalfDay.PM, "x"))
        );
    }

    @Test
    void weekendAndHalfDayCalculationsAreAuthoritative() {
        Fixture fixture = fixture();
        PendingActionView weekendRange = fixture.service.createPending(proposal(
                LocalDate.of(2026, 7, 17), LocalDate.of(2026, 7, 20), HalfDay.NONE, "x"), "o", ADMIN);
        assertEquals(new BigDecimal("2.0"), weekendRange.summary().days());

        PendingActionView half = fixture.service.createPending(proposal(
                LocalDate.of(2026, 7, 21), LocalDate.of(2026, 7, 21), HalfDay.AM, "x"), "o", ADMIN);
        assertEquals(new BigDecimal("0.5"), half.summary().days());
    }

    @Test
    void insufficientBalanceAndPendingCapacityAreRejected() {
        Fixture low = fixture(new BigDecimal("0.0"), 100, 600);
        assertCode("BUSINESS_RULE_VIOLATION",
                () -> low.service.createPending(standardProposal(), "o", ADMIN));

        Fixture capacity = fixture(new BigDecimal("5.0"), 1, 600);
        capacity.service.createPending(standardProposal(), "o", ADMIN);
        assertCode("ACTION_CAPACITY_EXCEEDED",
                () -> capacity.service.createPending(proposal(LocalDate.of(2026, 7, 21),
                        LocalDate.of(2026, 7, 21), HalfDay.NONE, "x"), "o", ADMIN));
    }

    @Test
    void confirmIsIdempotentAcrossSameAndDifferentKeys() {
        Fixture fixture = fixture();
        PendingActionView pending = fixture.service.createPending(standardProposal(), "origin", ADMIN);
        String firstKey = UUID.randomUUID().toString();
        ActionExecutionResponse first = fixture.service.confirm(pending.actionId(),
                pending.confirmationNonce(), firstKey, ADMIN, "confirm-1");
        ActionExecutionResponse same = fixture.service.confirm(pending.actionId(),
                pending.confirmationNonce(), firstKey, ADMIN, "confirm-2");
        ActionExecutionResponse different = fixture.service.confirm(pending.actionId(),
                pending.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "confirm-3");

        assertFalse(first.replayed());
        assertTrue(same.replayed());
        assertTrue(different.replayed());
        assertEquals(first.requestId(), same.requestId());
        assertEquals(first.requestId(), different.requestId());
        assertEquals(1, fixture.sandbox.requests().size());
        assertEquals(new BigDecimal("4.0"), fixture.sandbox.balance());
    }

    @Test
    void invalidNonceAndIdempotencyKeyDoNotExecute() {
        Fixture fixture = fixture();
        PendingActionView pending = fixture.service.createPending(standardProposal(), "origin", ADMIN);
        assertCode("INVALID_CONFIRMATION_NONCE", () -> fixture.service.confirm(
                pending.actionId(), "wrong", UUID.randomUUID().toString(), ADMIN, "t"));
        assertCode("INVALID_IDEMPOTENCY_KEY", () -> fixture.service.confirm(
                pending.actionId(), pending.confirmationNonce(), "not-uuid", ADMIN, "t"));
        assertEquals(0, fixture.sandbox.requests().size());
    }

    @Test
    void expiryCancelAndStateConflictsAreEnforced() {
        Fixture expired = fixture(new BigDecimal("5.0"), 100, 1);
        PendingActionView old = expired.service.createPending(standardProposal(), "origin", ADMIN);
        expired.clock.advance(Duration.ofSeconds(2));
        assertCode("ACTION_EXPIRED", () -> expired.service.confirm(old.actionId(),
                old.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "t"));

        Fixture cancelled = fixture();
        PendingActionView pending = cancelled.service.createPending(standardProposal(), "origin", ADMIN);
        ActionExecutionResponse first = cancelled.service.cancel(
                pending.actionId(), pending.confirmationNonce(), ADMIN, "cancel-1");
        ActionExecutionResponse replay = cancelled.service.cancel(
                pending.actionId(), pending.confirmationNonce(), ADMIN, "cancel-2");
        assertFalse(first.replayed());
        assertTrue(replay.replayed());
        assertCode("ACTION_STATE_CONFLICT", () -> cancelled.service.confirm(pending.actionId(),
                pending.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "t"));
    }

    @Test
    void processingStateReturnsConflict() {
        Fixture fixture = fixture();
        PendingActionView view = fixture.service.createPending(standardProposal(), "origin", ADMIN);
        PendingAction stored = fixture.repository.find(view.actionId()).orElseThrow();
        synchronized (stored) {
            stored.markProcessing(UUID.randomUUID());
        }
        assertCode("ACTION_IN_PROGRESS", () -> fixture.service.confirm(view.actionId(),
                view.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "t"));
    }

    @Test
    void balanceAndConflictChangesMakePendingActionStale() {
        Fixture balanceFixture = fixture();
        PendingActionView first = balanceFixture.service.createPending(standardProposal(), "o1", ADMIN);
        PendingActionView second = balanceFixture.service.createPending(proposal(
                LocalDate.of(2026, 7, 21), LocalDate.of(2026, 7, 21), HalfDay.NONE, "x"), "o2", ADMIN);
        balanceFixture.service.confirm(second.actionId(), second.confirmationNonce(),
                UUID.randomUUID().toString(), ADMIN, "t2");
        assertCode("ACTION_STALE", () -> balanceFixture.service.confirm(first.actionId(),
                first.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "t1"));

        Fixture conflictFixture = fixture();
        PendingActionView overlap1 = conflictFixture.service.createPending(standardProposal(), "o1", ADMIN);
        PendingActionView overlap2 = conflictFixture.service.createPending(standardProposal(), "o2", ADMIN);
        conflictFixture.service.confirm(overlap2.actionId(), overlap2.confirmationNonce(),
                UUID.randomUUID().toString(), ADMIN, "t2");
        assertCode("ACTION_STALE", () -> conflictFixture.service.confirm(overlap1.actionId(),
                overlap1.confirmationNonce(), UUID.randomUUID().toString(), ADMIN, "t1"));
        assertCode("BUSINESS_RULE_VIOLATION",
                () -> conflictFixture.service.createPending(standardProposal(), "o3", ADMIN));
    }

    @Test
    void concurrentDoubleConfirmCreatesExactlyOneLeaveRequest() throws Exception {
        Fixture fixture = fixture();
        PendingActionView pending = fixture.service.createPending(standardProposal(), "origin", ADMIN);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Object> one = executor.submit(() -> confirmConcurrent(fixture, pending, ready, start));
            Future<Object> two = executor.submit(() -> confirmConcurrent(fixture, pending, ready, start));
            ready.await();
            start.countDown();
            List<Object> results = List.of(one.get(), two.get());
            assertEquals(2, results.size());
            assertEquals(1, fixture.sandbox.requests().size());
            assertEquals(new BigDecimal("4.0"), fixture.sandbox.balance());
        } finally {
            executor.shutdownNow();
        }
    }

    private Object confirmConcurrent(Fixture fixture, PendingActionView pending,
                                     CountDownLatch ready, CountDownLatch start) throws Exception {
        ready.countDown();
        start.await();
        try {
            return fixture.service.confirm(pending.actionId(), pending.confirmationNonce(),
                    UUID.randomUUID().toString(), ADMIN, UUID.randomUUID().toString());
        } catch (ActionException exception) {
            return exception.errorCode();
        }
    }

    private static AnnualLeaveActionProposal standardProposal() {
        return proposal(LocalDate.of(2026, 7, 20), LocalDate.of(2026, 7, 20), HalfDay.NONE, "私事");
    }

    private static AnnualLeaveActionProposal proposal(LocalDate start, LocalDate end,
                                                       HalfDay halfDay, String reason) {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                start, end, reason, halfDay);
    }

    private static Fixture fixture() { return fixture(new BigDecimal("5.0"), 100, 600); }

    private static Fixture fixture(BigDecimal balance, int maxPending, long ttl) {
        BusinessActionProperties properties = new BusinessActionProperties();
        properties.setEnabled(true);
        properties.setRequireAdmin(true);
        properties.setDemoAnnualLeaveBalance(balance);
        properties.setMaxPending(maxPending);
        properties.setTtlSeconds(ttl);
        MutableClock clock = new MutableClock(Instant.parse("2026-07-16T00:00:00Z"),
                ZoneId.of("Asia/Shanghai"));
        InMemoryPendingActionRepository repository = new InMemoryPendingActionRepository(properties, clock);
        LeaveSandboxService sandbox = new LeaveSandboxService(properties, clock);
        BusinessActionService service = new BusinessActionService(properties,
                new AdminAccessService(ADMIN), repository, new ActionNonceService(), sandbox, clock);
        return new Fixture(properties, clock, repository, sandbox, service);
    }

    private static void assertCode(String code, org.junit.jupiter.api.function.Executable executable) {
        assertEquals(code, assertThrows(ActionException.class, executable).errorCode());
    }

    private record Fixture(BusinessActionProperties properties, MutableClock clock,
                           InMemoryPendingActionRepository repository,
                           LeaveSandboxService sandbox, BusinessActionService service) {}

    private static final class MutableClock extends Clock {
        private Instant instant;
        private final ZoneId zone;
        private MutableClock(Instant instant, ZoneId zone) { this.instant = instant; this.zone = zone; }
        void advance(Duration duration) { instant = instant.plus(duration); }
        @Override public ZoneId getZone() { return zone; }
        @Override public Clock withZone(ZoneId zone) { return new MutableClock(instant, zone); }
        @Override public Instant instant() { return instant; }
    }
}
