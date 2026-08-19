package com.fantuan.copilot.dto.auth;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record LoginRequest(
        @NotBlank(message = "username 不能为空")
        @Size(max = 64, message = "username 长度不能超过64")
        String username,
        @NotBlank(message = "password 不能为空")
        @Size(max = 128, message = "password 长度不能超过128")
        String password) {
}
