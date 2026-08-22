package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
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
        "business.actions.enabled=true",
        "business.actions.require-admin=false",
        "demo.identity.enabled=true"
})
class DemoIdentityIsolationIntegrationTest extends PostgresIntegrationTestBase {
    @Autowired BusinessActionService service;
    @Autowired DemoIdentityService identities;
    @Autowired LeaveAccountRepository accounts;
    @Autowired LeaveRequestRepository requests;
    @Autowired PendingActionRepository actions;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE leave_request_number_seq RESTART WITH 1");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0");
    }

    @Test
    void draftsAreBoundToEachServerSideIdentity() {
        assertOwner(create(user("DEMO-001"), nextWeekday(2)), "DEMO-001", "Demo User");
        assertOwner(create(user("DEMO-002"), nextWeekday(3)), "DEMO-002", "Demo User B");
        assertOwner(create(user("DEMO-MGR-001"), nextWeekday(4)),
                "DEMO-MGR-001", "Demo Manager");
    }

    @Test
    void sameDateIsAllowedAcrossUsersButBalanceAndConflictStayPerEmployee() {
        LocalDate date = nextWeekday(2);
        DemoIdentity a = user("DEMO-001");
        DemoIdentity b = user("DEMO-002");
        confirm(create(a, date), a);
        confirm(create(b, date), b);

        assertEquals(new BigDecimal("4.0"), accounts.findBalance(a.employeeId()).orElseThrow());
        assertEquals(new BigDecimal("4.0"), accounts.findBalance(b.employeeId()).orElseThrow());
        assertEquals(new BigDecimal("5.0"), accounts.findBalance("DEMO-MGR-001").orElseThrow());
        assertEquals(2, requests.size());

        ActionException conflict = assertThrows(ActionException.class, () -> create(a, date));
        assertEquals("BUSINESS_RULE_VIOLATION", conflict.errorCode());
    }

    @Test
    void crossUserConfirmIsIndistinguishableFromMissingAndDoesNotConsumeDraft() {
        DemoIdentity a = user("DEMO-001");
        DemoIdentity b = user("DEMO-002");
        PendingActionView pending = create(a, nextWeekday(2));

        ActionException managerDenied = assertThrows(ActionException.class, () -> service.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "manager-cross-user", user("DEMO-MGR-001")));
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
        DemoIdentity a = user("DEMO-001");
        PendingActionView forUserB = create(user("DEMO-002"), nextWeekday(2));
        PendingActionView forManager = create(user("DEMO-MGR-001"), nextWeekday(3));

        assertNotFound(() -> service.cancel(forUserB.actionId(), forUserB.confirmationNonce(),
                null, "a-cancel-b", a));
        assertNotFound(() -> service.cancel(forManager.actionId(), forManager.confirmationNonce(),
                null, "a-cancel-manager", a));
        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(forUserB.actionId()).orElseThrow().status());

        service.cancel(forUserB.actionId(), forUserB.confirmationNonce(), null,
                "b-cancel", user("DEMO-002"));
        assertEquals(ActionStatus.CANCELLED,
                actions.find(forUserB.actionId()).orElseThrow().status());
    }

    private PendingActionView create(DemoIdentity identity, LocalDate date) {
        return service.createPending(new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, date, date,
                "identity isolation test", HalfDay.NONE), "origin", null, identity, null);
    }

    private void confirm(PendingActionView pending, DemoIdentity identity) {
        service.confirm(pending.actionId(), pending.confirmationNonce(),
                UUID.randomUUID().toString(), null, "confirm", identity);
    }

    private DemoIdentity user(String id) {
        return identities.requireIdentity(id);
    }

    private void assertOwner(PendingActionView view, String employeeId, String displayName) {
        var action = actions.find(view.actionId()).orElseThrow();
        assertEquals(employeeId, action.employeeId());
        assertEquals(displayName, action.displayName());
        assertEquals(displayName, view.summary().employee());
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
        assertEquals(new BigDecimal(a), accounts.findBalance("DEMO-001").orElseThrow());
        assertEquals(new BigDecimal(b), accounts.findBalance("DEMO-002").orElseThrow());
        assertEquals(new BigDecimal(manager), accounts.findBalance("DEMO-MGR-001").orElseThrow());
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
