package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.auth.AuthErrorResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 全局异常处理器：仅处理输入校验相关异常。
 * Python 服务异常 fallback 语义由各 Controller 自行处理，不在此接管。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<?> handleValidationException(
            MethodArgumentNotValidException ex,
            HttpServletRequest request) {

        String traceId = (String) request.getAttribute("traceId");
        if (traceId == null) {
            traceId = "unknown";
        }

        // 收集所有校验错误信息
        String errorMessage = ex.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .collect(Collectors.joining("; "));

        log.warn("[{}] 输入校验失败: {}", traceId, errorMessage);

        String path = request.getRequestURI();

        if (path.startsWith("/api/auth/")) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                    .cacheControl(org.springframework.http.CacheControl.noStore())
                    .body(new AuthErrorResponse("INVALID_REQUEST", "请求格式无效。", traceId));
        }

        // /api/agent/langgraph/chat 返回 AgentChatResponse 兼容格式
        if (path.contains("/agent/langgraph/")) {
            Map<String, Object> body = Map.of(
                    "answer", "请求参数校验失败: " + errorMessage,
                    "route", "error",
                    "safe", true,
                    "category", "input_error",
                    "reason", errorMessage,
                    "sources", List.of(),
                    "success", false,
                    "traceId", traceId
            );
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
        }

        // /api/chat 返回 ChatResponse 兼容格式
        Map<String, Object> body = Map.of(
                "answer", "请求参数校验失败: " + errorMessage,
                "model", "unknown",
                "traceId", traceId,
                "success", false
        );
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }
}
