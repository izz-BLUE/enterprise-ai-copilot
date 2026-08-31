package com.fantuan.copilot.service.action;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadExecutionGuard;
import com.fantuan.copilot.service.agent.AgentRuntimeThreadIdService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.task.TaskRuntimeException;
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BusinessActionHitlCoordinatorTaskRuntimeTest {
    private static final String ACTION_ID = "act-task-1";
    private static final String TASK_ID = "task-1";
    private static final String USER_ID = "user-1";
    private static final String EMPLOYEE_ID = "E10001";
    private static final String CONVERSATION_ID = "conversation-1";
    private static final String THREAD_ID = "rt-task-thread";

    @Mock BusinessActionService actionService;
    @Mock PendingActionRepository actions;
    @Mock PythonAgentGateway pythonAgentGateway;
    @Mock AgentRuntimeThreadIdService threadIdService;
    @Mock AgentRuntimeThreadExecutionGuard threadGuard;
    @Mock AdminAccessService adminAccessService;
    @Mock ExpenseExternalApprovalCoordinator externalApprovalCoordinator;
    @Mock TaskRuntimeService taskRuntimeService;
    @Mock AiTaskMemoryService memoryService;

    @Test
    void taskRuntimeExpenseConfirmEndsCurrentGraphAndUsesJavaExternalHandoff() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        PendingAction action = org.mockito.Mockito.mock(PendingAction.class);
        when(action.actionId()).thenReturn(ACTION_ID);
        when(action.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        when(action.ownerUserId()).thenReturn(USER_ID);
        when(action.conversationId()).thenReturn(CONVERSATION_ID);
        when(action.agentExecutionId()).thenReturn("ex_" + "a".repeat(32));
        when(action.hitlWaitId()).thenReturn("wait_" + "b".repeat(64));

        TaskExecution task = new TaskExecution("group-1", TASK_ID, USER_ID,
                CONVERSATION_ID, 1, TaskType.EXPENSE_CLAIM, "把出差报销掉", null,
                TaskExecutionStatus.WAITING_USER, ACTION_ID, Instant.now(), Instant.now(), null);
        ActionExecutionResponse committed = new ActionExecutionResponse(ACTION_ID,
                BusinessActionType.EXPENSE_CLAIM, ActionStatus.SUCCEEDED, "EXP-1",
                "已提交。", false, Instant.now(), "origin", "trace");

        when(actions.find(ACTION_ID)).thenReturn(Optional.of(action));
        when(taskRuntimeService.findByActionId(ACTION_ID)).thenReturn(Optional.of(task));
        when(taskRuntimeService.startNextRunnable("group-1")).thenReturn(Optional.empty());
        when(threadIdService.generate(USER_ID, CONVERSATION_ID)).thenReturn(THREAD_ID);
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, TASK_ID)).thenReturn(THREAD_ID);
        when(threadGuard.tryAcquire(THREAD_ID)).thenReturn(true);
        when(actionService.confirm(anyString(), anyString(), anyString(), anyString(),
                anyString(), any())).thenReturn(committed);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(externalApprovalCoordinator.registerTaskRuntimeAndDispatch(
                eq(action), eq(committed), eq("trace"))).thenReturn(true);
        when(pythonAgentGateway.post(eq("/agent/langgraph/hitl/resume"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("已提交。", "action", true,
                        "business_action", "", List.of(), true, "trace", null,
                        List.of(), null));

        BusinessActionHitlCoordinator coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null,
                taskRuntimeService, null);

        ActionExecutionResponse actual = coordinator.confirm(ACTION_ID, "nonce", "idem",
                "admin", "trace", identity);

        assertEquals(committed, actual);
        assertNull(actual.nextPendingAction());
        ArgumentCaptor<HttpHeaders> headers = ArgumentCaptor.forClass(HttpHeaders.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), any(),
                headers.capture(), eq(PythonAgentResponse.class), eq("trace"));
        assertEquals("TASK_RUNTIME", headers.getValue().getFirst("X-Agent-Execution-Mode"));
        assertEquals(TASK_ID, headers.getValue().getFirst("X-Agent-Task-Id"));
        verify(externalApprovalCoordinator).registerTaskRuntimeAndDispatch(
                action, committed, "trace");
        verify(pythonAgentGateway, never()).post(eq("/agent/langgraph/external/resume"),
                any(), any(HttpHeaders.class), eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void nextTaskFailureDoesNotPersistItsMemoryProposal() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = new TaskExecution("group-1", TASK_ID, USER_ID,
                CONVERSATION_ID, 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.COMPLETED, null, Instant.now(), Instant.now(), Instant.now());
        TaskExecution next = new TaskExecution("group-1", "task-2", USER_ID,
                CONVERSATION_ID, 2, TaskType.EXPENSE_CLAIM, "报销", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AgentMemoryProposal proposal = new AgentMemoryProposal(
                "EXPENSE_CLAIM", java.util.Map.of("step", "invalid"), "should not survive");

        when(taskRuntimeService.startNextRunnable("group-1")).thenReturn(Optional.of(next));
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn(THREAD_ID);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(pythonAgentGateway.post(eq("/agent/langgraph/chat"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("失败", "action", true,
                        "business_action", "", List.of(), true, "trace", null, List.of(), proposal));
        when(taskRuntimeService.markTerminal("task-2", TaskExecutionStatus.FAILED))
                .thenReturn(true);

        BusinessActionHitlCoordinator coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null,
                taskRuntimeService, memoryService);

        assertNull(coordinator.startNextTaskAfterTerminal(current, identity, "admin", "trace"));
        verify(taskRuntimeService).markTerminal("task-2", TaskExecutionStatus.FAILED);
        verify(memoryService).abandon(USER_ID, CONVERSATION_ID);
        verify(memoryService, never()).upsertActiveForNextTask(
                anyString(), anyString(), anyString(), any(), anyString());
        var order = org.mockito.Mockito.inOrder(taskRuntimeService, memoryService);
        order.verify(taskRuntimeService).markTerminal("task-2", TaskExecutionStatus.FAILED);
        order.verify(memoryService).abandon(USER_ID, CONVERSATION_ID);
    }

    @Test
    void deterministicRejectionCarriesSuccessorWithoutRequeue() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = task(TASK_ID, 1, TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.RUNNING);
        TaskExecution successor = task("task-2", 2, TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.RUNNING);
        AnnualLeaveActionProposal leaveProposal = leaveProposal();
        HitlWaitMarker leaveWait = wait(BusinessActionType.ANNUAL_LEAVE_REQUEST, "a");
        BusinessActionProposal expenseProposal = mock(BusinessActionProposal.class);
        when(expenseProposal.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        HitlWaitMarker expenseWait = wait(BusinessActionType.EXPENSE_CLAIM, "c");
        PendingActionView successorPending = pendingView(
                "expense-action", BusinessActionType.EXPENSE_CLAIM);
        ActionException rejection = deterministicRejection();

        when(taskRuntimeService.findByTaskId(TASK_ID)).thenReturn(Optional.of(current));
        when(taskRuntimeService.startNextRunnable("group-1"))
                .thenReturn(Optional.of(successor));
        when(taskRuntimeService.matchesTaskType(successor, TaskType.EXPENSE_CLAIM))
                .thenReturn(true);
        when(taskRuntimeService.markTerminal(TASK_ID, TaskExecutionStatus.FAILED))
                .thenReturn(true);
        when(taskRuntimeService.markWaitingUser("task-2", successorPending.actionId()))
                .thenReturn(true);
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, TASK_ID))
                .thenReturn("t1-thread");
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn("t2-thread");
        when(actionService.createHitlPending(
                leaveProposal, "trace", "admin", identity, CONVERSATION_ID,
                leaveWait.executionId(), leaveWait.waitId()))
                .thenThrow(rejection);
        when(actionService.createHitlPending(
                expenseProposal, "trace", "admin", identity, CONVERSATION_ID,
                expenseWait.executionId(), expenseWait.waitId()))
                .thenReturn(successorPending);
        when(actions.findByHitlWaitId(expenseWait.waitId())).thenReturn(Optional.empty());
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(pythonAgentGateway.post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), eq("trace")))
                .thenAnswer(invocation -> "/agent/langgraph/chat".equals(invocation.getArgument(0))
                        ? new PythonAgentResponse("请确认报销", "action", true,
                        "business_action", "", List.of(), true, "trace", expenseProposal,
                        List.of(), null, expenseWait, null)
                        : new PythonAgentResponse("已拒绝", "action", true,
                        "business_action", "", List.of(), true, "trace", null, List.of(), null));

        TaskRuntimeRegistrationRejectionException exception = assertThrows(
                TaskRuntimeRegistrationRejectionException.class,
                () -> coordinator(memoryService).registerWait(leaveProposal, leaveWait,
                        "trace", "admin", identity, CONVERSATION_ID, TASK_ID));

        assertSame(rejection, exception.rejection());
        assertSame(successorPending, exception.successorPendingAction());
        verify(taskRuntimeService).markTerminal(TASK_ID, TaskExecutionStatus.FAILED);
        verify(taskRuntimeService).markWaitingUser("task-2", successorPending.actionId());
        verify(taskRuntimeService, never()).markWaitingUser(TASK_ID, successorPending.actionId());
        verify(taskRuntimeService, never()).requeueAfterLaunchFailure("task-2");
        verify(actionService).createHitlPending(
                expenseProposal, "trace", "admin", identity, CONVERSATION_ID,
                expenseWait.executionId(), expenseWait.waitId());
    }

    @Test
    void deterministicRejectionWithoutSuccessorKeepsSafeFailureContinuation() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = task(TASK_ID, 1, TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.RUNNING);
        AnnualLeaveActionProposal proposal = leaveProposal();
        HitlWaitMarker wait = wait(BusinessActionType.ANNUAL_LEAVE_REQUEST, "a");
        ActionException rejection = deterministicRejection();

        when(taskRuntimeService.findByTaskId(TASK_ID)).thenReturn(Optional.of(current));
        when(taskRuntimeService.startNextRunnable("group-1"))
                .thenReturn(Optional.empty());
        when(taskRuntimeService.markTerminal(TASK_ID, TaskExecutionStatus.FAILED))
                .thenReturn(true);
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, TASK_ID))
                .thenReturn("t1-thread");
        when(actionService.createHitlPending(
                proposal, "trace", "admin", identity, CONVERSATION_ID,
                wait.executionId(), wait.waitId()))
                .thenThrow(rejection);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(pythonAgentGateway.post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("已拒绝", "action", true,
                        "business_action", "", List.of(), true, "trace", null, List.of(), null));

        TaskRuntimeRegistrationRejectionException exception = assertThrows(
                TaskRuntimeRegistrationRejectionException.class,
                () -> coordinator(memoryService).registerWait(proposal, wait, "trace", "admin",
                        identity, CONVERSATION_ID, TASK_ID));

        assertSame(rejection, exception.rejection());
        assertNull(exception.successorPendingAction());
        verify(taskRuntimeService).markTerminal(TASK_ID, TaskExecutionStatus.FAILED);
        verify(taskRuntimeService, never()).requeueAfterLaunchFailure(anyString());
    }

    @Test
    void transientSuccessorLaunchFailureRemainsRetryable() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = task(TASK_ID, 1, TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.RUNNING);
        TaskExecution successor = task("task-2", 2, TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.RUNNING);
        AnnualLeaveActionProposal proposal = leaveProposal();
        HitlWaitMarker wait = wait(BusinessActionType.ANNUAL_LEAVE_REQUEST, "a");
        ActionException rejection = deterministicRejection();

        when(taskRuntimeService.findByTaskId(TASK_ID)).thenReturn(Optional.of(current));
        when(taskRuntimeService.startNextRunnable("group-1"))
                .thenReturn(Optional.of(successor));
        when(taskRuntimeService.markTerminal(TASK_ID, TaskExecutionStatus.FAILED))
                .thenReturn(true);
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, TASK_ID))
                .thenReturn("t1-thread");
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn("t2-thread");
        when(actionService.createHitlPending(
                proposal, "trace", "admin", identity, CONVERSATION_ID,
                wait.executionId(), wait.waitId()))
                .thenThrow(rejection);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(pythonAgentGateway.post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), eq("trace")))
                .thenAnswer(invocation -> "/agent/langgraph/chat".equals(invocation.getArgument(0))
                        ? throwRuntime("python unavailable")
                        : new PythonAgentResponse("已拒绝", "action", true,
                        "business_action", "", List.of(), true, "trace", null, List.of(), null));

        TaskRuntimeRegistrationRejectionException exception = assertThrows(
                TaskRuntimeRegistrationRejectionException.class,
                () -> coordinator(memoryService).registerWait(proposal, wait, "trace", "admin",
                        identity, CONVERSATION_ID, TASK_ID));

        assertSame(rejection, exception.rejection());
        assertNull(exception.successorPendingAction());
        verify(taskRuntimeService).requeueAfterLaunchFailure("task-2");
        verify(taskRuntimeService, never()).markTerminal("task-2", TaskExecutionStatus.FAILED);
    }

    @Test
    void nestedDeterministicSuccessorRejectionDoesNotRequeueTerminalSuccessor() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = task(TASK_ID, 1, TaskType.LEAVE_REQUEST,
                TaskExecutionStatus.RUNNING);
        TaskExecution successor = task("task-2", 2, TaskType.EXPENSE_CLAIM,
                TaskExecutionStatus.RUNNING);
        AnnualLeaveActionProposal leaveProposal = leaveProposal();
        HitlWaitMarker leaveWait = wait(BusinessActionType.ANNUAL_LEAVE_REQUEST, "a");
        BusinessActionProposal expenseProposal = mock(BusinessActionProposal.class);
        when(expenseProposal.actionType()).thenReturn(BusinessActionType.EXPENSE_CLAIM);
        HitlWaitMarker expenseWait = wait(BusinessActionType.EXPENSE_CLAIM, "c");
        ActionException t1Rejection = deterministicRejection();
        ActionException t2Rejection = new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "EXPENSE_AMOUNT_INVALID", "费用明细金额无效。", null, null);

        when(taskRuntimeService.findByTaskId(TASK_ID)).thenReturn(Optional.of(current));
        when(taskRuntimeService.findByTaskId("task-2")).thenReturn(Optional.of(successor));
        when(taskRuntimeService.startNextRunnable("group-1"))
                .thenReturn(Optional.of(successor), Optional.empty());
        when(taskRuntimeService.markTerminal(TASK_ID, TaskExecutionStatus.FAILED))
                .thenReturn(true);
        when(taskRuntimeService.markTerminal("task-2", TaskExecutionStatus.FAILED))
                .thenReturn(true);
        when(taskRuntimeService.matchesTaskType(successor, TaskType.EXPENSE_CLAIM))
                .thenReturn(true);
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, TASK_ID))
                .thenReturn("t1-thread");
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn("t2-thread");
        when(actionService.createHitlPending(
                leaveProposal, "trace", "admin", identity, CONVERSATION_ID,
                leaveWait.executionId(), leaveWait.waitId()))
                .thenThrow(t1Rejection);
        when(actionService.createHitlPending(
                expenseProposal, "trace", "admin", identity, CONVERSATION_ID,
                expenseWait.executionId(), expenseWait.waitId()))
                .thenThrow(t2Rejection);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(pythonAgentGateway.post(anyString(), any(), any(HttpHeaders.class),
                eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("已拒绝", "action", true,
                        "business_action", "", List.of(), true, "trace", expenseProposal,
                        List.of(), null, expenseWait, null));

        TaskRuntimeRegistrationRejectionException exception = assertThrows(
                TaskRuntimeRegistrationRejectionException.class,
                () -> coordinator(memoryService).registerWait(leaveProposal, leaveWait,
                        "trace", "admin", identity, CONVERSATION_ID, TASK_ID));

        assertSame(t1Rejection, exception.rejection());
        assertNull(exception.successorPendingAction());
        verify(taskRuntimeService).markTerminal(TASK_ID, TaskExecutionStatus.FAILED);
        verify(taskRuntimeService).markTerminal("task-2", TaskExecutionStatus.FAILED);
        verify(taskRuntimeService, never()).requeueAfterLaunchFailure("task-2");
    }

    @Test
    void nextTaskPersistsMemoryOnlyAfterWaitingUserTransition() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = new TaskExecution("group-1", TASK_ID, USER_ID,
                CONVERSATION_ID, 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.COMPLETED, null, Instant.now(), Instant.now(), Instant.now());
        TaskExecution next = new TaskExecution("group-1", "task-2", USER_ID,
                CONVERSATION_ID, 2, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AnnualLeaveActionProposal proposal = new AnnualLeaveActionProposal(
                BusinessActionType.ANNUAL_LEAVE_REQUEST, LocalDate.of(2026, 9, 1),
                LocalDate.of(2026, 9, 1), "私事", com.fantuan.copilot.model.action.HalfDay.NONE);
        HitlWaitMarker wait = new HitlWaitMarker(1, "BUSINESS_ACTION_CONFIRMATION",
                "wait_" + "b".repeat(64), "ex_" + "a".repeat(32),
                BusinessActionType.ANNUAL_LEAVE_REQUEST);
        PendingActionView pending = new PendingActionView("act-2",
                BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.PENDING_CONFIRMATION,
                "请假", null, "nonce", Instant.now(), true);
        AgentMemoryProposal memory = new AgentMemoryProposal(
                "LEAVE_REQUEST", java.util.Map.of("task", 2), "等待确认");

        when(taskRuntimeService.startNextRunnable("group-1")).thenReturn(Optional.of(next));
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn(THREAD_ID);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(pythonAgentGateway.post(eq("/agent/langgraph/chat"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("请确认", "action", true,
                        "business_action", "", List.of(), true, "trace", proposal,
                        List.of(), memory, wait, null));
        when(taskRuntimeService.matchesTaskType(next, TaskType.LEAVE_REQUEST)).thenReturn(true);
        when(actionService.createHitlPending(eq(proposal), eq("trace"), eq("admin"),
                eq(identity), eq(CONVERSATION_ID), eq(wait.executionId()), eq(wait.waitId())))
                .thenReturn(pending);
        when(actions.findByHitlWaitId(wait.waitId())).thenReturn(Optional.empty());
        when(taskRuntimeService.markWaitingUser("task-2", "act-2")).thenReturn(true);

        BusinessActionHitlCoordinator coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null,
                taskRuntimeService, memoryService);

        assertEquals(pending, coordinator.startNextTaskAfterTerminal(
                current, identity, "admin", "trace"));
        var order = org.mockito.Mockito.inOrder(taskRuntimeService, memoryService);
        order.verify(taskRuntimeService).markWaitingUser("task-2", "act-2");
        order.verify(memoryService).upsertActiveForNextTask(USER_ID, CONVERSATION_ID,
                "LEAVE_REQUEST", java.util.Map.of("task", 2), "等待确认");
    }

    @Test
    void nextTaskPersistsMemoryOnlyAfterClarificationTransition() {
        VerifiedIdentity identity = new VerifiedIdentity(USER_ID, "user", EMPLOYEE_ID,
                "用户", AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
        TaskExecution current = new TaskExecution("group-1", TASK_ID, USER_ID,
                CONVERSATION_ID, 1, TaskType.LEAVE_REQUEST, "请假", null,
                TaskExecutionStatus.COMPLETED, null, Instant.now(), Instant.now(), Instant.now());
        TaskExecution next = new TaskExecution("group-1", "task-2", USER_ID,
                CONVERSATION_ID, 2, TaskType.EXPENSE_CLAIM, "报销", null,
                TaskExecutionStatus.RUNNING, null, Instant.now(), Instant.now(), null);
        AgentMemoryProposal memory = new AgentMemoryProposal(
                "EXPENSE_CLAIM", java.util.Map.of("waiting_for", "invoice"), "等待补充");

        when(taskRuntimeService.startNextRunnable("group-1")).thenReturn(Optional.of(next));
        when(threadIdService.generate(USER_ID, CONVERSATION_ID, "task-2"))
                .thenReturn(THREAD_ID);
        when(actionService.isAllowed(eq("admin"), eq(identity))).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(adminAccessService.isAdminIdentity(identity)).thenReturn(true);
        when(pythonAgentGateway.post(eq("/agent/langgraph/chat"), any(),
                any(HttpHeaders.class), eq(PythonAgentResponse.class), eq("trace")))
                .thenReturn(new PythonAgentResponse("请补充发票", "action", true,
                        "business_action", "", List.of(), true, "trace", null,
                        List.of("invoice"), memory));
        when(taskRuntimeService.markWaitingClarification("task-2")).thenReturn(true);

        BusinessActionHitlCoordinator coordinator = new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null,
                taskRuntimeService, memoryService);

        assertNull(coordinator.startNextTaskAfterTerminal(current, identity, "admin", "trace"));
        var order = org.mockito.Mockito.inOrder(taskRuntimeService, memoryService);
        order.verify(taskRuntimeService).markWaitingClarification("task-2");
        order.verify(memoryService).upsertActiveForNextTask(USER_ID, CONVERSATION_ID,
                "EXPENSE_CLAIM", java.util.Map.of("waiting_for", "invoice"), "等待补充");
    }

    private BusinessActionHitlCoordinator coordinator(AiTaskMemoryService memoryService) {
        return new BusinessActionHitlCoordinator(
                actionService, actions, pythonAgentGateway, threadIdService, threadGuard,
                adminAccessService, externalApprovalCoordinator, null,
                taskRuntimeService, memoryService);
    }

    private static TaskExecution task(String taskId, int sequenceNo, TaskType taskType,
                                      TaskExecutionStatus status) {
        return new TaskExecution("group-1", taskId, USER_ID, CONVERSATION_ID, sequenceNo,
                taskType, taskType.name(), null, status, null,
                Instant.now(), Instant.now(), null);
    }

    private static AnnualLeaveActionProposal leaveProposal() {
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                LocalDate.of(2026, 9, 1), LocalDate.of(2026, 9, 1), "私事",
                com.fantuan.copilot.model.action.HalfDay.NONE);
    }

    private static HitlWaitMarker wait(BusinessActionType actionType, String suffix) {
        return new HitlWaitMarker(1, "BUSINESS_ACTION_CONFIRMATION",
                "wait_" + suffix.repeat(64), "ex_" + suffix.repeat(32), actionType);
    }

    private static PendingActionView pendingView(String actionId, BusinessActionType actionType) {
        return new PendingActionView(actionId, actionType, ActionStatus.PENDING_CONFIRMATION,
                actionType.name(), null, "nonce", Instant.now(), true);
    }

    private static ActionException deterministicRejection() {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", "日期范围与已提交的模拟申请冲突。", null, null);
    }

    private static PythonAgentResponse throwRuntime(String message) {
        throw new TaskRuntimeException(message);
    }
}
