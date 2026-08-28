package com.fantuan.copilot.model.task;

public enum TaskExecutionStatus {
    PENDING,
    RUNNING,
    WAITING_CLARIFICATION,
    WAITING_USER,
    WAITING_EXTERNAL,
    COMPLETED,
    FAILED,
    CANCELLED,
    EXPIRED,
    REJECTED;

    public boolean isTerminal() {
        return switch (this) {
            case COMPLETED, FAILED, CANCELLED, EXPIRED, REJECTED -> true;
            default -> false;
        };
    }

    public boolean blocksNextTask() {
        return switch (this) {
            case PENDING, RUNNING, WAITING_CLARIFICATION, WAITING_USER -> true;
            default -> false;
        };
    }
}
