package com.fantuan.copilot.service.task;

import com.fantuan.copilot.dto.task.TaskDecompositionResponse;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.repository.task.TaskExecutionRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.annotation.Propagation;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * Minimal Java-owned runtime for the bounded Leave/Expense multi-task case.
 * It owns ordering and lifecycle only; domain handlers remain authoritative.
 */
@Service
public class TaskRuntimeService {
    private static final int MAX_TASKS = 2;
    private static final int MAX_TASK_TEXT = 2000;
    private static final int MAX_CLARIFICATION_CONTEXT = 4000;

    private final TaskExecutionRepository executions;
    private final PythonAgentGateway pythonAgentGateway;
    private final Clock clock;

    @Autowired
    public TaskRuntimeService(TaskExecutionRepository executions,
                              PythonAgentGateway pythonAgentGateway,
                              Clock clock) {
        this.executions = executions;
        this.pythonAgentGateway = pythonAgentGateway;
        this.clock = clock;
    }

    @Transactional
    public Optional<TaskExecution> findInteractive(String ownerUserId, String conversationId) {
        return executions.findInteractiveByOwnerAndConversationForUpdate(
                ownerUserId, conversationId);
    }

    /**
     * Java-owned admission and recovery point for a conversation.
     *
     * The order is deliberately deterministic: an existing WAITING_USER or
     * WAITING_CLARIFICATION wins over RUNNING recovery, then an existing
     * RUNNING task is retried on its stable task thread, and only then may a
     * non-blocked PENDING task be promoted.  No model output participates in
     * this decision.
     */
    @Transactional
    public Optional<TaskExecution> reconcile(String ownerUserId, String conversationId) {
        List<TaskExecution> rows = executions.findByOwnerAndConversationForUpdate(
                ownerUserId, conversationId);
        for (TaskExecutionStatus status : List.of(
                TaskExecutionStatus.WAITING_USER,
                TaskExecutionStatus.WAITING_CLARIFICATION,
                TaskExecutionStatus.RUNNING)) {
            Optional<TaskExecution> current = rows.stream()
                    .filter(task -> task.status() == status)
                    .findFirst();
            if (current.isPresent()) {
                return current;
            }
        }

        Map<String, List<TaskExecution>> groups = new LinkedHashMap<>();
        for (TaskExecution row : rows) {
            groups.computeIfAbsent(row.taskGroupId(), ignored -> new ArrayList<>()).add(row);
        }
        for (List<TaskExecution> group : groups.values()) {
            for (TaskExecution candidate : group) {
                if (candidate.status() != TaskExecutionStatus.PENDING) {
                    continue;
                }
                boolean blocked = group.stream()
                        .filter(previous -> previous.sequenceNo() < candidate.sequenceNo())
                        .anyMatch(previous -> previous.status().blocksNextTask());
                if (blocked) {
                    break;
                }
                Instant now = clock.instant();
                if (executions.updateStatus(candidate.taskId(), TaskExecutionStatus.PENDING,
                        TaskExecutionStatus.RUNNING, now, null)) {
                    return executions.findByTaskId(candidate.taskId());
                }
            }
        }
        return Optional.empty();
    }

    /**
     * Synchronizes orchestration state with a committed Java business action.
     * MANDATORY makes an accidental post-commit invocation fail fast instead
     * of creating the exact half-committed state this runtime is avoiding.
     * Missing task rows are the intentional legacy-single path.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public boolean synchronizeBusinessStatus(String actionId, TaskExecutionStatus target) {
        if (actionId == null || actionId.isBlank()) {
            return true;
        }
        Optional<TaskExecution> current = executions.findByActionIdForUpdate(actionId);
        if (current.isEmpty()) {
            return true;
        }
        TaskExecution task = current.get();
        if (task.status() == target) {
            return true;
        }
        if (task.status().isTerminal()) {
            return false;
        }
        Instant now = clock.instant();
        return executions.updateStatusByActionId(actionId, target, now,
                target.isTerminal() ? now : null);
    }

    /** Re-queue only a task that failed before its next durable wait was registered. */
    public boolean requeueAfterLaunchFailure(String taskId) {
        Instant now = clock.instant();
        return executions.updateStatus(taskId, TaskExecutionStatus.RUNNING,
                TaskExecutionStatus.PENDING, now, null);
    }

    /** Cheap admission hint; Python remains the only decomposition parser. */
    public boolean isCompositeWriteCandidate(String message) {
        if (message == null) {
            return false;
        }
        String value = message.toLowerCase();
        if (value.contains("制度") || value.contains("流程") || value.contains("规定")
                || value.contains("政策") || value.contains("怎么") || value.contains("如何")) {
            return false;
        }
        boolean leave = value.contains("年假") || value.contains("请假")
                || value.contains("休假") || value.contains("请个假");
        boolean expense = value.contains("报销") || value.contains("报账");
        return leave && expense;
    }

    @Transactional
    public TaskExecution decomposeAndStart(String message, String ownerUserId,
                                            String conversationId, HttpHeaders headers,
                                            String traceId) {
        TaskDecompositionResponse response;
        try {
            response = pythonAgentGateway.post(
                    "/agent/tasks/decompose", new DecompositionRequest(message), headers,
                    TaskDecompositionResponse.class, traceId);
        } catch (RuntimeException exception) {
            throw new TaskRuntimeException("任务分解服务不可用，请稍后重试。", exception);
        }
        if (response == null) {
            throw new TaskRuntimeException("任务分解结果为空，请稍后重试。");
        }
        if ("unsupported".equals(response.kind())) {
            throw new TaskRuntimeException(firstNonBlank(response.reason(),
                    "当前消息包含无法安全拆分的多个业务动作，请分开提交。"));
        }
        List<TaskDecompositionResponse.TaskSpec> specs = response.tasks();
        if (!"multi".equals(response.kind()) || specs == null || specs.size() != MAX_TASKS) {
            throw new TaskRuntimeException("当前消息未形成可安全执行的双任务，请拆分后重试。");
        }
        validateSpecs(message, specs);

        Instant now = clock.instant();
        String groupId = "taskgrp_" + UUID.randomUUID().toString().replace("-", "");
        List<TaskExecution> rows = new ArrayList<>(MAX_TASKS);
        for (TaskDecompositionResponse.TaskSpec spec : specs) {
            rows.add(new TaskExecution(groupId,
                    "task_" + UUID.randomUUID().toString().replace("-", ""),
                    ownerUserId, conversationId, spec.sequence(),
                    TaskType.valueOf(spec.taskType()), spec.taskText(), null,
                    TaskExecutionStatus.PENDING, null, now, now, null));
        }
        executions.saveAll(rows);
        TaskExecution first = rows.get(0);
        if (!executions.updateStatus(first.taskId(), TaskExecutionStatus.PENDING,
                TaskExecutionStatus.RUNNING, now, null)) {
            throw new TaskRuntimeException("任务启动状态冲突，请稍后重试。");
        }
        return new TaskExecution(first.taskGroupId(), first.taskId(), first.ownerUserId(),
                first.conversationId(), first.sequenceNo(), first.taskType(), first.taskText(),
                first.clarificationContext(), TaskExecutionStatus.RUNNING, first.actionId(),
                first.createdAt(), now, first.completedAt());
    }

    @Transactional
    public Optional<TaskExecution> acceptClarification(TaskExecution waiting,
                                                       String clarification) {
        if (waiting == null || waiting.status() != TaskExecutionStatus.WAITING_CLARIFICATION
                || clarification == null || clarification.isBlank()) {
            return Optional.empty();
        }
        String existing = waiting.clarificationContext() == null
                ? "" : waiting.clarificationContext().trim();
        String next = existing.isEmpty() ? clarification.trim()
                : existing + "\n" + clarification.trim();
        if (next.length() > MAX_CLARIFICATION_CONTEXT) {
            throw new TaskRuntimeException("补充信息过长，请精简后重试。");
        }
        Instant now = clock.instant();
        if (!executions.updateClarificationContext(waiting.taskId(), next, now)) {
            throw new TaskRuntimeException("任务补充信息状态冲突，请稍后重试。");
        }
        return executions.findByTaskId(waiting.taskId());
    }

    public boolean markWaitingClarification(String taskId) {
        Instant now = clock.instant();
        return executions.updateStatus(taskId, TaskExecutionStatus.RUNNING,
                TaskExecutionStatus.WAITING_CLARIFICATION, now, null);
    }

    public boolean linkAction(String taskId, String actionId) {
        return executions.linkAction(taskId, actionId, clock.instant());
    }

    public boolean markWaitingUser(String taskId, String actionId) {
        Optional<TaskExecution> current = executions.findByTaskId(taskId);
        if (current.isPresent() && current.get().status() == TaskExecutionStatus.WAITING_USER
                && actionId != null && actionId.equals(current.get().actionId())) {
            return true;
        }
        if (!linkAction(taskId, actionId)) {
            return false;
        }
        Instant now = clock.instant();
        return executions.updateStatus(taskId, TaskExecutionStatus.RUNNING,
                TaskExecutionStatus.WAITING_USER, now, null);
    }

    public boolean markWaitingExternalByAction(String actionId) {
        Instant now = clock.instant();
        return executions.updateStatusByActionId(actionId,
                TaskExecutionStatus.WAITING_EXTERNAL, now, null);
    }

    public boolean markTerminalByAction(String actionId, TaskExecutionStatus status) {
        if (!status.isTerminal()) {
            throw new IllegalArgumentException("TaskExecution target must be terminal");
        }
        Instant now = clock.instant();
        return executions.updateStatusByActionId(actionId, status, now, now);
    }

    public boolean markTerminal(String taskId, TaskExecutionStatus status) {
        if (!status.isTerminal()) {
            throw new IllegalArgumentException("TaskExecution target must be terminal");
        }
        Instant now = clock.instant();
        return executions.updateStatus(taskId, TaskExecutionStatus.RUNNING,
                status, now, now);
    }

    public boolean matchesTaskType(TaskExecution execution, TaskType taskType) {
        return execution != null && taskType != null && execution.taskType() == taskType;
    }

    public Optional<TaskExecution> findByActionId(String actionId) {
        return executions.findByActionId(actionId);
    }

    public Optional<TaskExecution> findByTaskId(String taskId) {
        return executions.findByTaskId(taskId);
    }

    @Transactional
    public Optional<TaskExecution> startNextRunnable(String taskGroupId) {
        List<TaskExecution> group = executions.findByGroup(taskGroupId);
        for (TaskExecution candidate : group) {
            if (candidate.status() != TaskExecutionStatus.PENDING) {
                continue;
            }
            boolean blocked = group.stream()
                    .filter(previous -> previous.sequenceNo() < candidate.sequenceNo())
                    .anyMatch(previous -> previous.status().blocksNextTask());
            if (blocked) {
                return Optional.empty();
            }
            Instant now = clock.instant();
            if (executions.updateStatus(candidate.taskId(), TaskExecutionStatus.PENDING,
                    TaskExecutionStatus.RUNNING, now, null)) {
                return executions.findByTaskId(candidate.taskId());
            }
        }
        return Optional.empty();
    }

    private void validateSpecs(String message, List<TaskDecompositionResponse.TaskSpec> specs) {
        if (message == null || message.isBlank()) {
            throw new TaskRuntimeException("原始消息不能为空。");
        }
        int previousEnd = -1;
        for (int index = 0; index < specs.size(); index++) {
            TaskDecompositionResponse.TaskSpec spec = specs.get(index);
            if (spec == null || spec.sequence() != index + 1 || spec.taskText() == null
                    || spec.taskText().isBlank() || spec.taskText().length() > MAX_TASK_TEXT) {
                throw new TaskRuntimeException("任务分解结果不符合安全契约。");
            }
            TaskType taskType;
            try {
                taskType = TaskType.valueOf(spec.taskType());
            } catch (RuntimeException exception) {
                throw new TaskRuntimeException("任务类型不受支持。");
            }
            if (taskType == null) {
                throw new TaskRuntimeException("任务类型不受支持。");
            }
            int start = message.indexOf(spec.taskText(), previousEnd + 1);
            if (start < 0 || (previousEnd >= 0 && start <= previousEnd)) {
                throw new TaskRuntimeException("任务文本不是原始消息中的有序连续片段。");
            }
            previousEnd = start + spec.taskText().length() - 1;
        }
        if (specs.get(0).taskType().equals(specs.get(1).taskType())) {
            throw new TaskRuntimeException("第一版仅支持 Leave 与 Expense 的有序组合。");
        }
    }

    private String firstNonBlank(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private record DecompositionRequest(String message) {
    }
}
