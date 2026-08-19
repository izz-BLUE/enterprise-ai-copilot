package com.fantuan.copilot.controller;

import com.fantuan.copilot.auth.AuthException;
import com.fantuan.copilot.dto.auth.AuthErrorResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice(assignableTypes = AuthController.class)
public class AuthExceptionHandler {
    @ExceptionHandler(AuthException.class)
    public ResponseEntity<AuthErrorResponse> handle(AuthException exception,
                                                     HttpServletRequest request) {
        Object traceId = request.getAttribute("traceId");
        AuthErrorResponse body = new AuthErrorResponse(exception.errorCode(),
                exception.getMessage(), traceId == null ? "unknown" : traceId.toString());
        return ResponseEntity.status(exception.status())
                .cacheControl(CacheControl.noStore())
                .body(body);
    }
}
