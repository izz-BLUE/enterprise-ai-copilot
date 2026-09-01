package com.fantuan.copilot.service.task;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.repository.task.TaskExecutionRepository;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "demo.auth.public-password=public-test-password",
        "demo.auth.interview-password=interview-test-password",
        "demo.auth.admin-password=admin-test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false",
        "business.actions.max-pending=1",
        "business.actions.max-completed=2"
})
class TaskRuntimeActionRetentionIntegrationTest extends PostgresIntegrationTestBase {
    private static final VerifiedIdentity USER = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);

    @Autowired BusinessActionService actionService;
    @Autowired PendingActionRepository actions;
    @Autowired TaskExecutionRepository taskExecutions;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM purchase_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'E10001'");
    }

    @Test
    void terminalTaskCorrelationIsReleasedWhenActionRetentionDeletesOldestRow() {
        assertEquals("SET NULL", jdbc.queryForObject("""
                SELECT delete_rule FROM information_schema.referential_constraints
                WHERE constraint_name = 'fk_task_execution_action'
                """, String.class));

        PendingActionView cancelled = actionService.createPending(
                leaveProposal(), "retention-cancelled", null, USER, "retention-cancelled");
        saveTask("group-cancelled", "task-cancelled", cancelled.actionId(),
                TaskType.LEAVE_REQUEST, TaskExecutionStatus.WAITING_USER);
        actionService.cancel(cancelled.actionId(), cancelled.confirmationNonce(),
                null, "retention-cancel", USER);

        PendingActionView expired = actionService.createPending(
                leaveProposal(), "retention-expired", null, USER, "retention-expired");
        saveTask("group-expired", "task-expired", expired.actionId(),
                TaskType.LEAVE_REQUEST, TaskExecutionStatus.WAITING_USER);
        jdbc.update("UPDATE business_action SET expires_at = NOW() - INTERVAL '1 second' "
                + "WHERE action_id = ?", expired.actionId());
        assertTrue(actionService.reconcileExpiredForChat(
                USER.userId(), "retention-expired", "retention-expire").isPresent());

        PendingActionView failed = actionService.createPending(
                expenseProposal(), "retention-failed", null, USER, "retention-failed");
        saveTask("group-failed", "task-failed", failed.actionId(),
                TaskType.EXPENSE_CLAIM, TaskExecutionStatus.WAITING_USER);
        assertThrows(ActionException.class, () -> actionService.failStaleConfirmation(
                failed.actionId(), failed.confirmationNonce(), null, "retention-fail",
                USER, "EXPENSE_INVOICE_STALE"));

        setCompletedAt(cancelled.actionId(), Instant.parse("2026-08-01T00:00:00Z"));
        setCompletedAt(expired.actionId(), Instant.parse("2026-08-02T00:00:00Z"));
        setCompletedAt(failed.actionId(), Instant.parse("2026-08-03T00:00:00Z"));

        PendingActionView next = actionService.createPending(
                leaveProposal(), "retention-next", null, USER, "retention-next");

        assertFalse(actions.find(cancelled.actionId()).isPresent());
        assertTrue(actions.find(expired.actionId()).isPresent());
        assertTrue(actions.find(failed.actionId()).isPresent());
        assertTrue(actions.find(next.actionId()).isPresent());
        assertEquals(3, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action", Integer.class));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM business_action
                WHERE status IN ('CANCELLED', 'EXPIRED', 'FAILED')
                """, Integer.class));

        TaskExecution cancelledTask = taskExecutions.findByTaskId("task-cancelled").orElseThrow();
        TaskExecution expiredTask = taskExecutions.findByTaskId("task-expired").orElseThrow();
        TaskExecution failedTask = taskExecutions.findByTaskId("task-failed").orElseThrow();
        assertEquals(TaskExecutionStatus.CANCELLED, cancelledTask.status());
        assertEquals(null, cancelledTask.actionId());
        assertEquals(TaskExecutionStatus.EXPIRED, expiredTask.status());
        assertEquals(expired.actionId(), expiredTask.actionId());
        assertEquals(TaskExecutionStatus.FAILED, failedTask.status());
        assertEquals(failed.actionId(), failedTask.actionId());
    }

    private void saveTask(String groupId, String taskId, String actionId,
                          TaskType type, TaskExecutionStatus status) {
        Instant now = Instant.now();
        taskExecutions.saveAll(List.of(new TaskExecution(
                groupId, taskId, USER.userId(), groupId, 1, type,
                type == TaskType.LEAVE_REQUEST ? "请假" : "报销", null,
                status, actionId, now, now, null)));
    }

    private void setCompletedAt(String actionId, Instant completedAt) {
        jdbc.update("UPDATE business_action SET completed_at = ? WHERE action_id = ?",
                Timestamp.from(completedAt), actionId);
    }

    private AnnualLeaveActionProposal leaveProposal() {
        LocalDate date = actionService.businessDate().plusDays(2);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                date, date, "retention test", HalfDay.NONE);
    }

    private ExpenseActionProposal expenseProposal() {
        return new ExpenseActionProposal(BusinessActionType.EXPENSE_CLAIM,
                "TRIP-RETENTION", List.of(new ExpenseActionProposal.ExpenseItemPayload(
                "TAXI", new BigDecimal("100"), "INV-RETENTION", "retention test")),
                new BigDecimal("100"), new BigDecimal("100"), "COST-RETENTION",
                "retention test", List.of("INV-RETENTION"), 1);
    }
}
