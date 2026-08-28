package com.fantuan.copilot.model.task;

import java.time.Instant;

/** Java-owned lifecycle row for one decomposed business task. */
public record TaskExecution(
        String taskGroupId,
        String taskId,
        String ownerUserId,
        String conversationId,
        int sequenceNo,
        TaskType taskType,
        String taskText,
        String clarificationContext,
        TaskExecutionStatus status,
        String actionId,
        Instant createdAt,
        Instant updatedAt,
        Instant completedAt) {

    public boolean isCurrentInteractiveTask() {
        return status == TaskExecutionStatus.RUNNING
                || status == TaskExecutionStatus.WAITING_CLARIFICATION
                || status == TaskExecutionStatus.WAITING_USER;
    }
}
