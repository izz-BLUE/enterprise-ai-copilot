package com.fantuan.copilot.dto.memory;

import com.fasterxml.jackson.annotation.JsonInclude;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record MemoryWriteErrorResponse(
        String errorCode,
        String message,
        String traceId
) {
}
