package com.fantuan.copilot.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record ChatRequest(
        @NotBlank(message = "message 不能为空")
        @Size(max = 2000, message = "message 长度不能超过 2000 字符")
        String message
) {
}
