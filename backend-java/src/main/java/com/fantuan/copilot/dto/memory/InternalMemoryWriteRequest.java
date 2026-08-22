package com.fantuan.copilot.dto.memory;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.util.Map;

/**
 * Java 内部 Memory Write API 请求 DTO（Phase 4B）。
 *
 * 用于：Python Agent (MemoryWriteDispatcher) → Java AiTaskMemoryService 写入。
 *
 * 严格 identity boundary：
 *  1. 不接受 userId / employeeId / conversationId / token / jwt / permission 等
 *     任何身份 / 鉴权字段。
 *  2. conversationId 必须通过 URL path 传入（{@code /api/internal/memory/conversations/{conversationId}/write}），
 *     作为 trusted request context 的一部分，由服务端校验正则 + 长度。
 *  3. userId 永远来自 Java 签发并验签的 MemoryWriteScope；DTO 不承载 userId。
 *
 * 字段白名单（与 Python JavaMemoryClient outbound payload 对齐）：
 *   - action / taskType / status / taskState / summary
 *
 * taskState（Map）由 Java 侧在 Service 层再做一次 trusted-key 剥离：
 * 禁止出现 userId / employeeId / token / jwt / password / nonce / idempotency_key / permission / role / allowEval / allowBusinessActions 等敏感 / 业务字段。
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record InternalMemoryWriteRequest(
        @NotBlank(message = "action 不能为空")
        @Pattern(regexp = "UPSERT|COMPLETE|ABANDON", message = "action 必须是 UPSERT/COMPLETE/ABANDON")
        String action,

        @Size(max = 64, message = "taskType 长度不能超过 64")
        String taskType,

        @NotBlank(message = "status 不能为空")
        @Pattern(regexp = "ACTIVE|COMPLETED|ABANDONED", message = "status 必须是 ACTIVE/COMPLETED/ABANDONED")
        String status,

        Map<String, Object> taskState,

        @Size(max = 500, message = "summary 长度不能超过 500")
        String summary
) {
}
