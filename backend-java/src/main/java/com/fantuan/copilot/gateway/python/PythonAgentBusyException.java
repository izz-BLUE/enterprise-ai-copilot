package com.fantuan.copilot.gateway.python;

/** Python Agent 并发槽在短等待窗口内不可用。 */
public final class PythonAgentBusyException extends RuntimeException {
    public PythonAgentBusyException() {
        super("Python Agent concurrency limit reached");
    }
}
