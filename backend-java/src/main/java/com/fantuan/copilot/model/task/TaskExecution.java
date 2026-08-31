package com.fantuan.copilot.model.task;

import java.time.Instant;

/** 一个已分解业务任务的 Java 所有生命周期记录。 */
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
