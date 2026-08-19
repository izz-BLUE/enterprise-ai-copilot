package com.fantuan.copilot.dto.auth;

public record AuthErrorResponse(String errorCode, String message, String traceId) {
}
