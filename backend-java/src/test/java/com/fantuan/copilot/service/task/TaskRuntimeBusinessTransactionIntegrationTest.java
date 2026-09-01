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
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "demo.auth.public-password=public-test-password",
        "demo.auth.interview-password=interview-test-password",
        "demo.auth.admin-password=admin-test-password",
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
    @Autowired TaskRuntimeService taskRuntimeService;
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
        jdbc.execute("DELETE FROM purchase_request");
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
    void taskRuntimeLeaveConfirmReplayDoesNotCloseSuccessorMemoryOrChangeSuccessor() {
        memoryService.upsert(USER.userId(), CONVERSATION, "LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{}", "leave task");
        PendingActionView first = actionService.createPending(
                leaveProposal(), "replay-leave-first", null, USER, CONVERSATION);
        saveTask("group-replay-leave", 1, first.actionId(), TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.WAITING_USER);
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "replay-leave-confirm", USER);

        PendingActionView second = actionService.createPending(
                expenseProposal(), "replay-leave-second", null, USER, CONVERSATION);
        saveTask("group-replay-leave", 2, second.actionId(), TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.WAITING_USER);
        memoryService.upsertActiveForNextTask(USER.userId(), CONVERSATION,
                "EXPENSE_CLAIM", java.util.Map.of("task", 2), "expense task");
        TaskExecution successorBefore = taskExecutions.findByTaskId("task-" + second.actionId())
                .orElseThrow();
        var memoryBefore = memoryService.find(USER.userId(), CONVERSATION).orElseThrow();

        ActionExecutionResponse replay = actionService.confirm(
                first.actionId(), first.confirmationNonce(), UUID.randomUUID().toString(),
                null, "replay-leave-again", USER);

        assertTrue(replay.replayed());
        assertEquals(successorBefore,
                taskExecutions.findByTaskId("task-" + second.actionId()).orElseThrow());
        var memoryAfter = memoryService.find(USER.userId(), CONVERSATION).orElseThrow();
        assertEquals(TaskStatus.ACTIVE, memoryAfter.status());
        assertEquals(memoryBefore.taskType(), memoryAfter.taskType());
        assertEquals(memoryBefore.taskStateJson(), memoryAfter.taskStateJson());
        assertEquals(memoryBefore.summary(), memoryAfter.summary());
    }

    @Test
    void taskRuntimeExpenseWaitingExternalReplayDoesNotCloseSuccessorMemory() {
        memoryService.upsert(USER.userId(), CONVERSATION, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "expense task");
        PendingActionView first = actionService.createPending(
                expenseProposal(), "replay-expense-first", null, USER, CONVERSATION);
        saveTask("group-replay-expense", 1, first.actionId(), TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.WAITING_USER);
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "replay-expense-confirm", USER);

        PendingActionView second = actionService.createPending(
                leaveProposal(), "replay-expense-second", null, USER, CONVERSATION);
        saveTask("group-replay-expense", 2, second.actionId(), TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.WAITING_USER);
        memoryService.upsertActiveForNextTask(USER.userId(), CONVERSATION,
                "LEAVE_REQUEST", java.util.Map.of("task", 2), "leave task");
        TaskExecution successorBefore = taskExecutions.findByTaskId("task-" + second.actionId())
                .orElseThrow();

        ActionExecutionResponse replay = actionService.confirm(
                first.actionId(), first.confirmationNonce(), UUID.randomUUID().toString(),
                null, "replay-expense-again", USER);

        assertTrue(replay.replayed());
        assertEquals(TaskExecutionStatus.WAITING_EXTERNAL,
                taskExecutions.findByActionId(first.actionId()).orElseThrow().status());
        assertEquals(successorBefore,
                taskExecutions.findByTaskId("task-" + second.actionId()).orElseThrow());
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
        assertEquals("LEAVE_REQUEST",
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().taskType());
    }

    @Test
    void taskRuntimeExpenseTerminalReplayRemainsIdempotent() {
        memoryService.upsert(USER.userId(), CONVERSATION, "EXPENSE_CLAIM",
                TaskStatus.ACTIVE, "{}", "expense task");
        PendingActionView first = actionService.createPending(
                expenseProposal(), "replay-terminal-first", null, USER, CONVERSATION);
        saveTask("group-replay-terminal", 1, first.actionId(), TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.WAITING_USER);
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "replay-terminal-confirm", USER);
        assertTrue(taskRuntimeService.markTerminalByAction(
                first.actionId(), TaskExecutionStatus.COMPLETED));

        PendingActionView second = actionService.createPending(
                leaveProposal(), "replay-terminal-second", null, USER, CONVERSATION);
        saveTask("group-replay-terminal", 2, second.actionId(), TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.WAITING_USER);
        memoryService.upsertActiveForNextTask(USER.userId(), CONVERSATION,
                "LEAVE_REQUEST", java.util.Map.of("task", 2), "leave task");

        ActionExecutionResponse replay = actionService.confirm(
                first.actionId(), first.confirmationNonce(), UUID.randomUUID().toString(),
                null, "replay-terminal-again", USER);

        assertTrue(replay.replayed());
        assertEquals(TaskExecutionStatus.COMPLETED,
                taskExecutions.findByActionId(first.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ACTIVE,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
        assertEquals("LEAVE_REQUEST",
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().taskType());
    }

    @Test
    void taskRuntimeSuccessorCanCreateAfterPreviousLeaveActionIsTerminal() {
        PendingActionView first = actionService.createPending(
                leaveProposal(), "runtime-first-leave", null, USER, CONVERSATION);
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "runtime-first-leave-confirm", USER);

        PendingActionView second = actionService.createPending(
                expenseProposal(), "runtime-second-expense", null, USER, CONVERSATION);

        assertEquals(ActionStatus.PENDING_CONFIRMATION, actionStatus(second.actionId()));
    }

    @Test
    void taskRuntimeSuccessorCanCreateAfterPreviousExpenseWaitsExternally() {
        PendingActionView first = actionService.createPending(
                expenseProposal(), "runtime-first-expense", null, USER, CONVERSATION);
        saveTask("group-runtime", 1, first.actionId(), TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.WAITING_USER);
        actionService.confirm(first.actionId(), first.confirmationNonce(),
                UUID.randomUUID().toString(), null, "runtime-first-expense-confirm", USER);

        PendingActionView second = actionService.createPending(
                leaveProposal(), "runtime-second-leave", null, USER, CONVERSATION);

        assertEquals(ActionStatus.PENDING_CONFIRMATION, actionStatus(second.actionId()));
        assertEquals(TaskExecutionStatus.WAITING_EXTERNAL,
                taskExecutions.findByActionId(first.actionId()).orElseThrow().status());
    }

    @Test
    void markWaitingUserRecoversRunningTaskWithExistingActionBinding() {
        PendingActionView pending = actionService.createPending(
                leaveProposal(), "runtime-recover", null, USER, CONVERSATION);
        saveTask("group-recover", 1, pending.actionId(), TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.RUNNING);

        String taskId = "task-" + pending.actionId();
        assertTrue(taskRuntimeService.markWaitingUser(taskId, pending.actionId()));

        TaskExecution recovered = taskExecutions.findByTaskId(taskId).orElseThrow();
        assertEquals(TaskExecutionStatus.WAITING_USER, recovered.status());
        assertEquals(pending.actionId(), recovered.actionId());
        assertEquals(1, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE conversation_id = ?",
                Integer.class, CONVERSATION));
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
        saveTask("group-transaction", 1, actionId, type, status);
    }

    private void saveTask(String groupId, int sequenceNo, String actionId,
                          TaskType type, TaskExecutionStatus status) {
        Instant now = Instant.now();
        taskExecutions.saveAll(List.of(new TaskExecution(
                groupId, "task-" + actionId, USER.userId(), CONVERSATION,
                sequenceNo, type, type == TaskType.LEAVE_REQUEST ? "请假" : "报销", null,
                status, actionId, now, now, status.isTerminal() ? now : null)));
    }

    private ActionStatus actionStatus(String actionId) {
        return ActionStatus.valueOf(jdbc.queryForObject(
                "SELECT status FROM business_action WHERE action_id = ?", String.class, actionId));
    }

    private AnnualLeaveActionProposal leaveProposal() {
        LocalDate start = actionService.businessDate().plusDays(2);
        while (start.getDayOfWeek() == DayOfWeek.SATURDAY
                || start.getDayOfWeek() == DayOfWeek.SUNDAY) {
            start = start.plusDays(1);
        }
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
