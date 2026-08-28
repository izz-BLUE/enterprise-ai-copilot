package com.fantuan.copilot.service.task;

public class TaskRuntimeException extends RuntimeException {
    public TaskRuntimeException(String message) {
        super(message);
    }

    public TaskRuntimeException(String message, Throwable cause) {
        super(message, cause);
    }
}
