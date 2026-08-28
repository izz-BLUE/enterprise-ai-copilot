package com.fantuan.copilot.repository.task;

import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface TaskExecutionRepository {
    void saveAll(List<TaskExecution> executions);

    Optional<TaskExecution> findByTaskId(String taskId);

    Optional<TaskExecution> findByTaskIdForUpdate(String taskId);

    Optional<TaskExecution> findByActionId(String actionId);

    Optional<TaskExecution> findByActionIdForUpdate(String actionId);

    Optional<TaskExecution> findInteractiveByOwnerAndConversationForUpdate(
            String ownerUserId, String conversationId);

    List<TaskExecution> findByOwnerAndConversationForUpdate(
            String ownerUserId, String conversationId);

    Optional<TaskExecution> findPendingByGroupAndSequenceForUpdate(
            String taskGroupId, int sequenceNo);

    List<TaskExecution> findByGroup(String taskGroupId);

    boolean updateStatus(String taskId, TaskExecutionStatus expected,
                         TaskExecutionStatus target, Instant updatedAt,
                         Instant completedAt);

    boolean updateStatusByActionId(String actionId, TaskExecutionStatus target,
                                   Instant updatedAt, Instant completedAt);

    boolean linkAction(String taskId, String actionId, Instant updatedAt);

    boolean updateClarificationContext(String taskId, String context, Instant updatedAt);
}
