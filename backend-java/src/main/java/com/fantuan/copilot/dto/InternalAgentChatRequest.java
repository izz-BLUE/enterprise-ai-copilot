package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * Java → Python Agent 服务的内部请求 DTO（仅用于内部跨服务调用，绝不暴露给前端）。
 *
 * 与公共 {@link ChatRequest} 的区别：
 *  1. 增加 memoryContext 字段：承载服务端读取的 ai_task_memory 上下文，
 *     由 (trusted user_id, conversation_id) 复合 key 唯一决定；
 *  2. 该字段不由前端提交；前端永远通过公共 ChatRequest 提交 message + conversationId。
 *
 * memoryContext 的内部契约：
 *  - 仅在 status=ACTIVE 时由服务端权威填充；
 *  - 字段白名单：taskType / status / taskStateJson / summary；
 *  - 不包含 userId / conversationId / nonce / idempotency_key 等敏感或业务字段；
 *  - 缺失或为空视为"无 Memory"，Python Planner 行为与历史一致。
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record InternalAgentChatRequest(
        @NotBlank(message = "message 不能为空")
        @Size(max = 2000, message = "message 长度不能超过 2000 字符")
        String message,

        MemoryContextView memoryContext,

        String taskId,

        String clarificationContext
) {

    public InternalAgentChatRequest(String message, MemoryContextView memoryContext) {
        this(message, memoryContext, null, null);
    }

    /**
     * Memory 视图（内部接口用）。大小上限与 ai_task_memory 表 CHECK 约束对齐：
     *  - taskType  上限 64
     *  - status    上限 32
     *  - taskStateJson 上限 16 KiB（octet_length）
     *  - summary   上限 500
     * 这些上限由 Java 服务端赋值前在 AiTaskMemoryService 中保证；
     * 本 DTO 不再做额外校验（避免重复维护）。
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record MemoryContextView(
            String taskType,
            String status,
            String taskStateJson,
            String summary
    ) {
    }
}
