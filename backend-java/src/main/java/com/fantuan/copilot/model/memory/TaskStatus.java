package com.fantuan.copilot.model.memory;

import java.util.Optional;

/**
 * 任务记忆状态。P0 阶段仅 3 个取值，与 ai_task_memory.status CHECK 约束一致。
 */
public enum TaskStatus {
    ACTIVE,
    COMPLETED,
    ABANDONED;

    public static Optional<TaskStatus> parse(String raw) {
        if (raw == null) {
            return Optional.empty();
        }
        try {
            return Optional.of(TaskStatus.valueOf(raw));
        } catch (IllegalArgumentException ex) {
            return Optional.empty();
        }
    }
}