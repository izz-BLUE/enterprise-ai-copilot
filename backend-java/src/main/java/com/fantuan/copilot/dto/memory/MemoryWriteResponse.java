package com.fantuan.copilot.dto.memory;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.Instant;

/**
 * Java 内部 Memory Write API 响应 DTO（Phase 4B）。
 *
 * 字段：
 *   - action: UPSERT / COMPLETE / ABANDON
 *   - taskType / status: 写入后的归一化值
 *   - updatedAt: 数据库实际 updated_at（写入即刻生效）
 *
 * 不暴露 userId / conversationId 等身份 / 会话字段（这些对 Python 调用方无意义）。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record MemoryWriteResponse(
        String action,
        String taskType,
        String status,
        Instant updatedAt
) {
}
