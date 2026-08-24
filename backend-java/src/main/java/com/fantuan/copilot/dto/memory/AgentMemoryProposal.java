package com.fantuan.copilot.dto.memory;

import com.fasterxml.jackson.annotation.JsonAlias;

import java.util.Map;

/**
 * Python 随 Agent 响应返回的非权威任务记忆提案。
 * owner、conversationId 和生命周期状态由 Java 当前请求上下文决定，不属于该契约。
 */
public record AgentMemoryProposal(
        @JsonAlias("task_type") String taskType,
        @JsonAlias("task_state") Map<String, Object> taskState,
        String summary) {
}
