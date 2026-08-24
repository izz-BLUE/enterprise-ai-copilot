package com.fantuan.copilot.service.agent;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;

/** Agent 管理员日志的安全、尽力而为记录器。 */
public final class AgentEventRecorder {
    private final AdminLogBuffer buffer;

    public AgentEventRecorder(AdminLogBuffer buffer) {
        this.buffer = buffer;
    }

    public void record(String traceId, String eventName, String level, long started) {
        try {
            long elapsed = (System.nanoTime() - started) / 1_000_000L;
            buffer.record(level == null ? AdminLogEvent.LEVEL_INFO : level,
                    AdminLogEvent.CATEGORY_AGENT, eventName, traceId,
                    null, null, null, null, elapsed, message(eventName));
        } catch (RuntimeException ignored) {
            // 可观测性旁路不得影响业务响应。
        }
    }

    private static String message(String eventName) {
        return switch (eventName) {
            case "AGENT_REQUEST_RECEIVED" -> "LangGraph Agent request received";
            case "AGENT_REQUEST_COMPLETED" -> "LangGraph Agent request completed";
            case "AGENT_REQUEST_FAILED" -> "LangGraph Agent request failed";
            default -> "LangGraph Agent event";
        };
    }
}
