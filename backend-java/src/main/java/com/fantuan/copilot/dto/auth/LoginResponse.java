package com.fantuan.copilot.dto.auth;

public record LoginResponse(
        String accessToken,
        String tokenType,
        long expiresIn,
        AuthUserResponse user) {
}
