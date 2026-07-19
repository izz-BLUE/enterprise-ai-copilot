package com.fantuan.copilot.service.action;

import com.fantuan.copilot.EnterpriseAiCopilotBackendApplication;
import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import com.fantuan.copilot.service.demo.DemoRole;
import org.junit.jupiter.api.Test;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

class BusinessActionRestartIntegrationTest extends PostgresIntegrationTestBase {
    private static final DemoIdentity USER_A = new DemoIdentity(
            "DEMO-001", "DEMO-001", "Demo User", DemoRole.EMPLOYEE);

    @Test
    void ownershipSurvivesRestartAndIsCheckedBeforeNonceConsumption() {
        PendingActionView pending;
        try (ConfigurableApplicationContext first = startContext("5.0")) {
            reset(first);
            BusinessActionService service = first.getBean(BusinessActionService.class);
            DemoIdentity a = first.getBean(DemoIdentityService.class).requireIdentity("DEMO-001");
            pending = service.createPending(proposal(nextWeekday(new TestActionService(service), 2)),
                    "restart-owner", null, a);
        }

        try (ConfigurableApplicationContext second = startContext("9.0")) {
            BusinessActionService service = second.getBean(BusinessActionService.class);
            DemoIdentityService identities = second.getBean(DemoIdentityService.class);
            ActionException denied = assertThrows(ActionException.class, () -> service.confirm(
                    pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "restart-b", identities.requireIdentity("DEMO-002")));
            assertEquals("ACTION_NOT_FOUND", denied.errorCode());
            ActionExecutionResponse confirmed = service.confirm(pending.actionId(),
                    pending.confirmationNonce(), UUID.randomUUID().toString(), null,
                    "restart-a", identities.requireIdentity("DEMO-001"));
            assertEquals(com.fantuan.copilot.model.action.ActionStatus.SUCCEEDED,
                    confirmed.status());
            assertEquals(new BigDecimal("4.0"), second.getBean(LeaveAccountRepository.class)
                    .findBalance("DEMO-001").orElseThrow());
            assertEquals(new BigDecimal("5.0"), second.getBean(LeaveAccountRepository.class)
                    .findBalance("DEMO-002").orElseThrow());
        }
    }

    @Test
    void pendingSuccessReplayAndCancellationSurviveContextRestarts() {
        PendingActionView pending;
        String firstKey = UUID.randomUUID().toString();
        try (ConfigurableApplicationContext first = startContext("5.0")) {
            reset(first);
            TestActionService service = service(first);
            pending = service.createPending(proposal(nextWeekday(service, 2)), "origin", null);
        }

        ActionExecutionResponse confirmed;
        PendingActionView cancelled;
        try (ConfigurableApplicationContext second = startContext("9.0")) {
            TestActionService service = service(second);
            assertEquals(new BigDecimal("5.0"), second.getBean(LeaveAccountRepository.class)
                    .findBalance("DEMO-001").orElseThrow());
            confirmed = service.confirm(pending.actionId(), pending.confirmationNonce(),
                    firstKey, null, "confirm");
            cancelled = service.createPending(proposal(nextWeekday(service, 3)), "origin-2", null);
            service.cancel(cancelled.actionId(), cancelled.confirmationNonce(), null, "cancel");
        }

        try (ConfigurableApplicationContext third = startContext("9.0")) {
            TestActionService service = service(third);
            ActionExecutionResponse sameKeyReplay = service.confirm(pending.actionId(),
                    pending.confirmationNonce(), firstKey, null, "same-key-replay");
            ActionExecutionResponse differentKeyReplay = service.confirm(pending.actionId(),
                    pending.confirmationNonce(), UUID.randomUUID().toString(), null, "different-key-replay");
            ActionExecutionResponse cancelReplay = service.cancel(cancelled.actionId(),
                    cancelled.confirmationNonce(), null, "cancel-replay");
            LeaveRequestRepository requests = third.getBean(LeaveRequestRepository.class);
            LeaveAccountRepository accounts = third.getBean(LeaveAccountRepository.class);
            assertTrue(sameKeyReplay.replayed());
            assertTrue(differentKeyReplay.replayed());
            assertEquals(confirmed.requestId(), sameKeyReplay.requestId());
            assertEquals(confirmed.requestId(), differentKeyReplay.requestId());
            assertEquals("origin", sameKeyReplay.originTraceId());
            assertEquals("same-key-replay", sameKeyReplay.traceId());
            assertEquals("different-key-replay", differentKeyReplay.traceId());
            assertTrue(cancelReplay.replayed());
            assertEquals(1, requests.countBySourceActionId(pending.actionId()));
            assertEquals(new BigDecimal("4.0"), accounts.findBalance("DEMO-001").orElseThrow());
        }
    }

    @Test
    void failedAndExpiredStatesSurviveContextRestart() {
        PendingActionView failed;
        PendingActionView expired;
        try (ConfigurableApplicationContext first = startContext("5.0")) {
            reset(first);
            TestActionService service = service(first);
            PendingActionView successful = service.createPending(
                    proposal(nextWeekday(service, 2)), "successful-origin", null);
            failed = service.createPending(proposal(nextWeekday(service, 3)), "failed-origin", null);
            service.confirm(successful.actionId(), successful.confirmationNonce(),
                    UUID.randomUUID().toString(), null, "successful-confirm");
            assertEquals("ACTION_STALE", assertThrows(ActionException.class, () -> service.confirm(
                    failed.actionId(), failed.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "failed-confirm")).errorCode());

            expired = service.createPending(proposal(nextWeekday(service, 4)), "expired-origin", null);
            first.getBean(JdbcTemplate.class).update(
                    "UPDATE business_action SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                            + "WHERE action_id = ?", expired.actionId());
            assertEquals("ACTION_EXPIRED", assertThrows(ActionException.class, () -> service.confirm(
                    expired.actionId(), expired.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "expired-confirm")).errorCode());
        }

        try (ConfigurableApplicationContext second = startContext("9.0")) {
            var repository = second.getBean(com.fantuan.copilot.repository.action.PendingActionRepository.class);
            assertEquals(com.fantuan.copilot.model.action.ActionStatus.FAILED,
                    repository.find(failed.actionId()).orElseThrow().status());
            assertEquals("ACTION_STALE", repository.find(failed.actionId()).orElseThrow().failureCode());
            assertEquals(com.fantuan.copilot.model.action.ActionStatus.EXPIRED,
                    repository.find(expired.actionId()).orElseThrow().status());
            TestActionService service = service(second);
            assertEquals("ACTION_STATE_CONFLICT", assertThrows(ActionException.class, () -> service.confirm(
                    failed.actionId(), failed.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "failed-retry")).errorCode());
            assertEquals("ACTION_EXPIRED", assertThrows(ActionException.class, () -> service.confirm(
                    expired.actionId(), expired.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "expired-retry")).errorCode());
        }
    }

    private ConfigurableApplicationContext startContext(String configuredBalance) {
        return new SpringApplicationBuilder(EnterpriseAiCopilotBackendApplication.class)
                .web(WebApplicationType.NONE)
                .run(
                        "--spring.datasource.url=" + POSTGRES.getJdbcUrl(),
                        "--spring.datasource.username=" + POSTGRES.getUsername(),
                        "--spring.datasource.password=" + POSTGRES.getPassword(),
                        "--business.actions.enabled=true",
                        "--business.actions.require-admin=false",
                        "--demo.identity.enabled=true",
                        "--business.actions.demo-annual-leave-balance=" + configuredBalance,
                        "--logging.level.root=WARN");
    }

    private void reset(ConfigurableApplicationContext context) {
        JdbcTemplate jdbc = context.getBean(JdbcTemplate.class);
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'DEMO-001'");
    }

    private AnnualLeaveActionProposal proposal(LocalDate date) {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                date, date, "restart integration test", HalfDay.NONE);
    }

    private TestActionService service(ConfigurableApplicationContext context) {
        return new TestActionService(context.getBean(BusinessActionService.class));
    }

    private LocalDate nextWeekday(TestActionService service, int offset) {
        LocalDate date = service.businessDate().plusDays(offset);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return date;
    }

    private record TestActionService(BusinessActionService delegate) {
        PendingActionView createPending(AnnualLeaveActionProposal proposal, String traceId,
                                        String adminToken) {
            return delegate.createPending(proposal, traceId, adminToken, USER_A);
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
