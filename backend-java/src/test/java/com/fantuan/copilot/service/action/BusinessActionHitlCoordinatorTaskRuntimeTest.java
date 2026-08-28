package com.fantuan.copilot.service.action;

import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
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
import com.fantuan.copilot.service.task.TaskRuntimeService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
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
        when(actionService.isAllowed("admin")).thenReturn(true);
        when(actionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 29));
        when(adminAccessService.isAdmin("admin")).thenReturn(true);
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
}
