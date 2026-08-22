package com.fantuan.copilot.model.memory;

import java.time.Instant;

/**
 * Scoped Conversation Memory / Task Continuity P0 —— 任务记忆。
 * 复合主键 (userId, conversationId) 保证同一用户同一会话只能有一条记录；
 * userId 来自服务端 VerifiedIdentity.userId()，conversationId 由客户端提供或服务端生成。
 *
 * 不在本对象中存储 JWT / 密码 / internal token / nonce / idempotency_key。
 */
public final class AiTaskMemory {
    private final String userId;
    private final String conversationId;
    private final String taskType;
    private final TaskStatus status;
    private final String taskStateJson;
    private final String summary;
    private final Instant createdAt;
    private final Instant updatedAt;

    public AiTaskMemory(String userId, String conversationId, String taskType, TaskStatus status,
                        String taskStateJson, String summary, Instant createdAt, Instant updatedAt) {
        this.userId = userId;
        this.conversationId = conversationId;
        this.taskType = taskType;
        this.status = status;
        this.taskStateJson = taskStateJson;
        this.summary = summary;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public String userId() { return userId; }
    public String conversationId() { return conversationId; }
    public String taskType() { return taskType; }
    public TaskStatus status() { return status; }
    public String taskStateJson() { return taskStateJson; }
    public String summary() { return summary; }
    public Instant createdAt() { return createdAt; }
    public Instant updatedAt() { return updatedAt; }
}