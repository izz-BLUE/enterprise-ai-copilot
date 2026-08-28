package com.fantuan.copilot.service.task;

import com.fantuan.copilot.dto.task.TaskDecompositionResponse;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.repository.task.TaskExecutionRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TaskRuntimeServiceTest {
    private static final Instant NOW = Instant.parse("2026-08-29T00:00:00Z");

    @Mock TaskExecutionRepository executions;
    @Mock PythonAgentGateway pythonAgentGateway;

    @Test
    void decompositionCreatesOrderedJavaOwnedTasksAndStartsOnlyFirst() {
        String message = "帮我请个假，然后把最近一次出差报销。";
        when(pythonAgentGateway.post(eq("/agent/tasks/decompose"), any(),
                any(HttpHeaders.class), eq(TaskDecompositionResponse.class), eq("trace")))
                .thenReturn(new TaskDecompositionResponse("multi", List.of(
                        new TaskDecompositionResponse.TaskSpec(
                                "LEAVE_REQUEST", "帮我请个假", 1),
                        new TaskDecompositionResponse.TaskSpec(
                                "EXPENSE_CLAIM", "把最近一次出差报销。", 2)), ""));
        when(executions.updateStatus(any(), eq(TaskExecutionStatus.PENDING),
                eq(TaskExecutionStatus.RUNNING), any(Instant.class), eq(null)))
                .thenReturn(true);

        TaskRuntimeService service = new TaskRuntimeService(executions, pythonAgentGateway,
                Clock.fixed(NOW, ZoneOffset.UTC));
        TaskExecution first = service.decomposeAndStart(message, "user-1", "conversation-1",
                new HttpHeaders(), "trace");

        ArgumentCaptor<List<TaskExecution>> captor = ArgumentCaptor.forClass(List.class);
        verify(executions).saveAll(captor.capture());
        assertEquals(2, captor.getValue().size());
        assertEquals(1, captor.getValue().get(0).sequenceNo());
        assertEquals(TaskExecutionStatus.RUNNING, first.status());
        assertEquals(TaskExecutionStatus.PENDING, captor.getValue().get(1).status());
        verify(executions).updateStatus(eq(captor.getValue().get(0).taskId()),
                eq(TaskExecutionStatus.PENDING), eq(TaskExecutionStatus.RUNNING),
                eq(NOW), eq(null));
    }

    @Test
    void decompositionRejectsTextThatIsNotAnOriginalOrderedSpan() {
        String message = "帮我请个假，然后把最近一次出差报销。";
        when(pythonAgentGateway.post(eq("/agent/tasks/decompose"), any(),
                any(HttpHeaders.class), eq(TaskDecompositionResponse.class), eq("trace")))
                .thenReturn(new TaskDecompositionResponse("multi", List.of(
                        new TaskDecompositionResponse.TaskSpec(
                                "LEAVE_REQUEST", "请年假", 1),
                        new TaskDecompositionResponse.TaskSpec(
                                "EXPENSE_CLAIM", "把最近一次出差报销。", 2)), ""));

        TaskRuntimeService service = new TaskRuntimeService(executions, pythonAgentGateway,
                Clock.fixed(NOW, ZoneOffset.UTC));

        assertThrows(TaskRuntimeException.class, () -> service.decomposeAndStart(
                message, "user-1", "conversation-1", new HttpHeaders(), "trace"));
        verify(executions, never()).saveAll(any());
    }

    @Test
    void clarificationIsBoundToExistingTaskAndReturnsItToRunning() {
        TaskExecution waiting = new TaskExecution(
                "group-1", "task-1", "user-1", "conversation-1", 1,
                com.fantuan.copilot.model.task.TaskType.LEAVE_REQUEST,
                "帮我请个假", null, TaskExecutionStatus.WAITING_CLARIFICATION,
                null, NOW, NOW, null);
        when(executions.updateClarificationContext(eq("task-1"), eq("2026年9月1日"), eq(NOW)))
                .thenReturn(true);
        when(executions.findByTaskId("task-1")).thenReturn(Optional.of(
                new TaskExecution("group-1", "task-1", "user-1", "conversation-1", 1,
                        com.fantuan.copilot.model.task.TaskType.LEAVE_REQUEST,
                        "帮我请个假", "2026年9月1日", TaskExecutionStatus.RUNNING,
                        null, NOW, NOW, null)));

        TaskRuntimeService service = new TaskRuntimeService(executions, pythonAgentGateway,
                Clock.fixed(NOW, ZoneOffset.UTC));

        assertEquals(TaskExecutionStatus.RUNNING,
                service.acceptClarification(waiting, "2026年9月1日").orElseThrow().status());
        verify(executions).updateClarificationContext("task-1", "2026年9月1日", NOW);
    }

    @Test
    void reconciliationStartsNonBlockedPendingSuccessorWithoutChangingGroup() {
        TaskExecution first = new TaskExecution(
                "group-1", "task-1", "user-1", "conversation-1", 1,
                com.fantuan.copilot.model.task.TaskType.LEAVE_REQUEST,
                "请假", null, TaskExecutionStatus.COMPLETED,
                "action-1", NOW, NOW, NOW);
        TaskExecution second = new TaskExecution(
                "group-1", "task-2", "user-1", "conversation-1", 2,
                com.fantuan.copilot.model.task.TaskType.EXPENSE_CLAIM,
                "报销", null, TaskExecutionStatus.PENDING,
                null, NOW, NOW, null);
        when(executions.findByOwnerAndConversationForUpdate("user-1", "conversation-1"))
                .thenReturn(List.of(first, second));
        when(executions.updateStatus("task-2", TaskExecutionStatus.PENDING,
                TaskExecutionStatus.RUNNING, NOW, null)).thenReturn(true);
        when(executions.findByTaskId("task-2")).thenReturn(Optional.of(
                new TaskExecution("group-1", "task-2", "user-1", "conversation-1", 2,
                        com.fantuan.copilot.model.task.TaskType.EXPENSE_CLAIM,
                        "报销", null, TaskExecutionStatus.RUNNING,
                        null, NOW, NOW, null)));

        TaskRuntimeService service = new TaskRuntimeService(executions, pythonAgentGateway,
                Clock.fixed(NOW, ZoneOffset.UTC));

        assertEquals("task-2", service.reconcile("user-1", "conversation-1")
                .orElseThrow().taskId());
        verify(executions).updateStatus("task-2", TaskExecutionStatus.PENDING,
                TaskExecutionStatus.RUNNING, NOW, null);
    }

    @Test
    void reconciliationReturnsRunningTaskForStableThreadRecovery() {
        TaskExecution running = new TaskExecution(
                "group-1", "task-1", "user-1", "conversation-1", 1,
                com.fantuan.copilot.model.task.TaskType.LEAVE_REQUEST,
                "请假", null, TaskExecutionStatus.RUNNING,
                null, NOW, NOW, null);
        when(executions.findByOwnerAndConversationForUpdate("user-1", "conversation-1"))
                .thenReturn(List.of(running));

        TaskRuntimeService service = new TaskRuntimeService(executions, pythonAgentGateway,
                Clock.fixed(NOW, ZoneOffset.UTC));

        assertEquals("task-1", service.reconcile("user-1", "conversation-1")
                .orElseThrow().taskId());
        verify(executions, never()).updateStatus(any(), eq(TaskExecutionStatus.PENDING),
                eq(TaskExecutionStatus.RUNNING), any(Instant.class), eq(null));
    }
}
