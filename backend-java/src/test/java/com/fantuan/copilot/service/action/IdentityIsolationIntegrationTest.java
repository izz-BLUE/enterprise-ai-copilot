package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.UUID;

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
class IdentityIsolationIntegrationTest extends PostgresIntegrationTestBase {
    @Autowired BusinessActionService service;
    @Autowired LeaveAccountRepository accounts;
    @Autowired LeaveRequestRepository requests;
    @Autowired PendingActionRepository actions;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0");
    }

    @Test
    void draftsAreBoundToEachServerSideIdentity() {
        assertOwner(create(user("E10001"), nextWeekday(2)), "E10001", "张三");
        assertOwner(create(user("E10002"), nextWeekday(3)), "E10002", "李四");
        assertOwner(create(user("E10003"), nextWeekday(4)), "E10003", "王五");
    }

    @Test
    void publicDemoIsReadOnlyEvenWhenBusinessActionsAreEnabled() {
        VerifiedIdentity publicDemo = new VerifiedIdentity(
                "U10000", "demo", "E10000", "公开演示账号",
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);

        assertFalse(service.isAllowed(null, publicDemo));
        assertTrue(service.isAllowed(null, user("E10001")));

        ActionException denied = assertThrows(ActionException.class,
                () -> create(publicDemo, nextWeekday(2)));
        assertEquals("BUSINESS_ACTIONS_NOT_ALLOWED", denied.errorCode());
        assertEquals(0, actions.size());
        assertEquals(0, requests.size());
        assertEquals(new BigDecimal("5.0"),
                accounts.findBalance(publicDemo.employeeId()).orElseThrow());
    }

    @Test
    void sameDateIsAllowedAcrossUsersButBalanceAndConflictStayPerEmployee() {
        LocalDate date = nextWeekday(2);
        VerifiedIdentity a = user("E10001");
        VerifiedIdentity b = user("E10002");
        confirm(create(a, date), a);
        confirm(create(b, date), b);

        assertEquals(new BigDecimal("4.0"), accounts.findBalance(a.employeeId()).orElseThrow());
        assertEquals(new BigDecimal("4.0"), accounts.findBalance(b.employeeId()).orElseThrow());
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("E10003").orElseThrow());
        assertEquals(2, requests.size());

        ActionException conflict = assertThrows(ActionException.class, () -> create(a, date));
        assertEquals("BUSINESS_RULE_VIOLATION", conflict.errorCode());
    }

    @Test
    void crossUserConfirmIsIndistinguishableFromMissingAndDoesNotConsumeDraft() {
        VerifiedIdentity a = user("E10001");
        VerifiedIdentity b = user("E10002");
        PendingActionView pending = create(a, nextWeekday(2));

        ActionException managerDenied = assertThrows(ActionException.class, () -> service.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "third-user-cross-user", user("E10003")));
        ActionException denied = assertThrows(ActionException.class, () -> service.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "cross-user", b));
        ActionException missing = assertThrows(ActionException.class, () -> service.confirm(
                "act_missing", pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "missing", b));

        assertSameExternalError(denied, missing);
        assertSameExternalError(managerDenied, missing);
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(0, requests.size());
        assertAllBalances("5.0", "5.0", "5.0");

        confirm(pending, a);
        assertEquals(1, requests.countBySourceActionId(pending.actionId()));
        assertAllBalances("4.0", "5.0", "5.0");
    }

    @Test
    void userAndManagerCannotCancelAnotherUsersDraft() {
        VerifiedIdentity a = user("E10001");
        PendingActionView forUserB = create(user("E10002"), nextWeekday(2));
        PendingActionView forManager = create(user("E10003"), nextWeekday(3));

        assertNotFound(() -> service.cancel(forUserB.actionId(), forUserB.confirmationNonce(),
                null, "a-cancel-b", a));
        assertNotFound(() -> service.cancel(forManager.actionId(), forManager.confirmationNonce(),
                null, "a-cancel-manager", a));
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(forUserB.actionId()).orElseThrow().status());

        service.cancel(forUserB.actionId(), forUserB.confirmationNonce(), null,
                "b-cancel", user("E10002"));
        assertEquals(ActionStatus.CANCELLED,
                actions.find(forUserB.actionId()).orElseThrow().status());
    }

    private PendingActionView create(VerifiedIdentity identity, LocalDate date) {
        return service.createPending(new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, date, date,
                "identity isolation test", HalfDay.NONE), "origin", null, identity, null);
    }

    private void confirm(PendingActionView pending, VerifiedIdentity identity) {
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm", identity);
    }

    private VerifiedIdentity user(String id) {
        String userId = switch (id) {
            case "E10001" -> "U10001";
            case "E10002" -> "U10002";
            case "E10003" -> "U10003";
            default -> throw new IllegalArgumentException("Unknown test identity");
        };
        String username = switch (id) {
            case "E10001" -> "zhangsan";
            case "E10002" -> "lisi";
            case "E10003" -> "wangwu";
            default -> throw new IllegalArgumentException("Unknown test identity");
        };
        String displayName = switch (id) {
            case "E10001" -> "张三";
            case "E10002" -> "李四";
            case "E10003" -> "王五";
            default -> throw new IllegalArgumentException("Unknown test identity");
        };
        return new VerifiedIdentity(userId, username, id, displayName,
                AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
    }

    private void assertOwner(PendingActionView view, String employeeId, String displayName) {
        var action = actions.find(view.actionId()).orElseThrow();
        assertEquals(employeeId, action.employeeId());
        assertEquals(displayName, action.displayName());
        // V2 §二十五: summary 多态为 Object，年假摘要强转 AnnualLeaveSummary。
        assertEquals(displayName,
                ((com.fantuan.copilot.dto.action.AnnualLeaveSummary) view.summary()).employee());
    }

    private void assertSameExternalError(ActionException first, ActionException second) {
        assertEquals(404, first.httpStatus().value());
        assertEquals(first.httpStatus(), second.httpStatus());
        assertEquals(first.errorCode(), second.errorCode());
        assertEquals(first.getMessage(), second.getMessage());
        assertNull(first.actionId());
        assertNull(second.actionId());
    }

    private void assertNotFound(org.junit.jupiter.api.function.Executable executable) {
        assertEquals("ACTION_NOT_FOUND",
                assertThrows(ActionException.class, executable).errorCode());
    }

    private void assertAllBalances(String a, String b, String manager) {
        assertEquals(new BigDecimal(a), accounts.findBalance("E10001").orElseThrow());
        assertEquals(new BigDecimal(b), accounts.findBalance("E10002").orElseThrow());
        assertEquals(new BigDecimal(manager), accounts.findBalance("E10003").orElseThrow());
    }

    private LocalDate nextWeekday(int offset) {
        LocalDate date = service.businessDate().plusDays(offset);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return date;
    }
}
