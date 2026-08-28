package com.fantuan.copilot.service.task;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.repository.task.TaskExecutionRepository;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class TaskRuntimeBusinessTransactionIntegrationTest extends PostgresIntegrationTestBase {
    private static final VerifiedIdentity USER = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
    private static final String CONVERSATION = "task-runtime-transaction";

    @Autowired BusinessActionService actionService;
    @Autowired TaskExecutionRepository taskExecutions;
    @Autowired AiTaskMemoryService memoryService;
    @Autowired LeaveRequestRepository leaveRequests;
    @Autowired ExpenseClaimRepository expenseClaims;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'E10001'");
    }

    @Test
    void leaveConfirmCommitsBusinessFactTaskTerminalAndMemoryTogether() {
        memoryService.upsert(USER.userId(), CONVERSATION, "LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{}", "leave task");
        PendingActionView pending = actionService.createPending(
                leaveProposal(), "transaction-leave", null, USER, CONVERSATION);
        saveTask(pending.actionId(), TaskType.LEAVE_REQUEST, TaskExecutionStatus.WAITING_USER);

        ActionExecutionResponse response = actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "transaction-leave-confirm", USER);

        assertEquals(ActionStatus.SUCCEEDED, response.status());
        assertEquals(ActionStatus.SUCCEEDED, actionStatus(pending.actionId()));
        assertEquals(1, leaveRequests.countBySourceActionId(pending.actionId()));
        assertEquals(TaskExecutionStatus.COMPLETED,
                taskExecutions.findByActionId(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
    }

    @Test
    void expenseConfirmCommitsClaimAndNonBlockingTaskRuntimeStateTogether() {
        memoryService.upsert(USER.userId(), CONVERSATION, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "expense task");
        PendingActionView pending = actionService.createPending(
                expenseProposal(), "transaction-expense", null, USER, CONVERSATION);
        saveTask(pending.actionId(), TaskType.EXPENSE_CLAIM, TaskExecutionStatus.WAITING_USER);

        ActionExecutionResponse response = actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "transaction-expense-confirm", USER);

        assertEquals(ActionStatus.SUCCEEDED, response.status());
        assertEquals(1, expenseClaims.countBySourceActionId(pending.actionId()));
        assertEquals(TaskExecutionStatus.WAITING_EXTERNAL,
                taskExecutions.findByActionId(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.COMPLETED,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
    }

    @Test
    void taskRuntimeTransitionConflictRollsBackBusinessFactAndMemory() {
        memoryService.upsert(USER.userId(), CONVERSATION, "LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{}", "leave task");
        PendingActionView pending = actionService.createPending(
                leaveProposal(), "transaction-rollback", null, USER, CONVERSATION);
        saveTask(pending.actionId(), TaskType.LEAVE_REQUEST, TaskExecutionStatus.CANCELLED);

        assertThrows(IllegalStateException.class, () -> actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "transaction-rollback-confirm", USER));

        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actionStatus(pending.actionId()));
        assertEquals(0, leaveRequests.countBySourceActionId(pending.actionId()));
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
    }

    private void saveTask(String actionId, TaskType type, TaskExecutionStatus status) {
        Instant now = Instant.now();
        taskExecutions.saveAll(List.of(new TaskExecution(
                "group-transaction", "task-" + actionId, USER.userId(), CONVERSATION,
                1, type, type == TaskType.LEAVE_REQUEST ? "请假" : "报销", null,
                status, actionId, now, now, status.isTerminal() ? now : null)));
    }

    private ActionStatus actionStatus(String actionId) {
        return ActionStatus.valueOf(jdbc.queryForObject(
                "SELECT status FROM business_action WHERE action_id = ?", String.class, actionId));
    }

    private AnnualLeaveActionProposal leaveProposal() {
        LocalDate start = LocalDate.of(2026, 9, 1);
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                start, start, "事务测试请假", HalfDay.NONE);
    }

    private ExpenseActionProposal expenseProposal() {
        return new ExpenseActionProposal(BusinessActionType.EXPENSE_CLAIM,
                "TRIP-TX-001", List.of(
                new ExpenseActionProposal.ExpenseItemPayload(
                        "TAXI", new BigDecimal("100"), "INV-TX-001", "事务测试交通")),
                new BigDecimal("100"), new BigDecimal("100"), "COST-TX",
                "事务测试报销", List.of("INV-TX-001"), 1);
    }
}
