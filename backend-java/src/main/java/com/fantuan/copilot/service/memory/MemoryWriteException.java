package com.fantuan.copilot.service.memory;

import org.springframework.http.HttpStatus;

/**
 * Memory Write API 业务异常（Phase 4B）。
 *
 * 错误码含义（与 HTTP status 一一对应）：
 *   - MEMORY_INTERNAL_TOKEN_REQUIRED (403): 服务间凭证缺失或无效
 *   - MEMORY_SCOPE_INVALID / MEMORY_SCOPE_MISMATCH (403): Java 签发 scope 无效或不匹配
 *   - MEMORY_TRUSTED_KEY_REJECTED (400): taskState 含 trusted 字段
 *   - MEMORY_PAYLOAD_INVALID (400): DTO 校验失败 / 必填字段缺失
 *   - MEMORY_CONVERSATION_ID_INVALID (400): path 传入的 conversationId 格式非法
 *   - MEMORY_INTERNAL_ERROR (500): 持久化 / 读取异常
 */
public class MemoryWriteException extends RuntimeException {

    private final HttpStatus httpStatus;
    private final String errorCode;

    public MemoryWriteException(HttpStatus httpStatus, String errorCode, String message) {
        super(message);
        this.httpStatus = httpStatus;
        this.errorCode = errorCode;
    }

    public HttpStatus httpStatus() {
        return httpStatus;
    }

    public String errorCode() {
        return errorCode;
    }
}
