package com.fantuan.copilot.service.action;

import com.fantuan.copilot.EnterpriseAiCopilotBackendApplication;
import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
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
    private static final VerifiedIdentity USER_A = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);

    @Test
    void ownershipSurvivesRestartAndIsCheckedBeforeNonceConsumption() {
        PendingActionView pending;
        try (ConfigurableApplicationContext first = startContext("5.0")) {
            reset(first);
            BusinessActionService service = first.getBean(BusinessActionService.class);
            pending = service.createPending(proposal(nextWeekday(new TestActionService(service), 2)),
                    "restart-owner", null, USER_A, null);
        }

        try (ConfigurableApplicationContext second = startContext("9.0")) {
            BusinessActionService service = second.getBean(BusinessActionService.class);
            ActionException denied = assertThrows(ActionException.class, () -> service.confirm(
                    pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                    null, "restart-b", new VerifiedIdentity(
                            "U10002", "lisi", "E10002", "李四",
                            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT)));
            assertEquals("ACTION_NOT_FOUND", denied.errorCode());
            ActionExecutionResponse confirmed = service.confirm(pending.actionId(),
                    pending.confirmationNonce(), UUID.randomUUID().toString(), null,
                    "restart-a", USER_A);
            assertEquals(com.fantuan.copilot.model.action.ActionStatus.SUCCEEDED,
                    confirmed.status());
            assertEquals(new BigDecimal("4.0"), second.getBean(LeaveAccountRepository.class)
                    .findBalance("E10001").orElseThrow());
            assertEquals(new BigDecimal("5.0"), second.getBean(LeaveAccountRepository.class)
                    .findBalance("E10002").orElseThrow());
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
                    .findBalance("E10001").orElseThrow());
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
            assertEquals(new BigDecimal("4.0"), accounts.findBalance("E10001").orElseThrow());
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
                        "--demo.auth.enabled=true",
                        "--demo.auth.default-password=test-password",
                        "--demo.auth.public-password=public-test-password",
                        "--demo.auth.interview-password=interview-test-password",
                        "--demo.auth.admin-password=admin-test-password",
                        "--business.actions.enabled=true",
                        "--business.actions.require-admin=false",
                        "--business.actions.demo-annual-leave-balance=" + configuredBalance,
                        "--logging.level.org.springframework=WARN",
                        "--logging.level.org.flywaydb=WARN",
                        "--logging.level.com.zaxxer.hikari=WARN");
    }

    private void reset(ConfigurableApplicationContext context) {
        JdbcTemplate jdbc = context.getBean(JdbcTemplate.class);
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM purchase_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'E10001'");
    }

    private AnnualLeaveActionProposal proposal(LocalDate date) {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                date, date, "restart integration test", HalfDay.NONE);
    }

    private TestActionService service(ConfigurableApplicationContext context) {
        return new TestActionService(context.getBean(BusinessActionService.class));
    }

    private LocalDate nextWeekday(TestActionService service, int offset) {
        LocalDate date = service.businessDate();
        int workdays = 0;
        while (workdays < offset) {
            date = date.plusDays(1);
            DayOfWeek dow = date.getDayOfWeek();
            if (dow != DayOfWeek.SATURDAY && dow != DayOfWeek.SUNDAY) {
                workdays++;
            }
        }
        return date;
    }

    private record TestActionService(BusinessActionService delegate) {
        PendingActionView createPending(AnnualLeaveActionProposal proposal, String traceId,
                                        String adminToken) {
            return delegate.createPending(proposal, traceId, adminToken, USER_A, null);
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
