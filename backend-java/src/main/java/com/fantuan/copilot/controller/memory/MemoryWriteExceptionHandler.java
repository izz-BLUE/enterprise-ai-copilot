package com.fantuan.copilot.controller.memory;

import com.fantuan.copilot.dto.memory.MemoryWriteErrorResponse;
import com.fantuan.copilot.service.memory.MemoryWriteException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@Order(Ordered.HIGHEST_PRECEDENCE)
@RestControllerAdvice(assignableTypes = MemoryWriteController.class)
public class MemoryWriteExceptionHandler {

    @ExceptionHandler(MemoryWriteException.class)
    public ResponseEntity<MemoryWriteErrorResponse> handleMemoryWrite(MemoryWriteException exception,
                                                                      HttpServletRequest request) {
        MemoryWriteErrorResponse body = new MemoryWriteErrorResponse(
                exception.errorCode(), exception.getMessage(), traceId(request));
        return ResponseEntity.status(exception.httpStatus())
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, HttpMessageNotReadableException.class})
    public ResponseEntity<MemoryWriteErrorResponse> handleInvalidRequest(Exception exception,
                                                                       HttpServletRequest request) {
        MemoryWriteErrorResponse body = new MemoryWriteErrorResponse(
                "MEMORY_PAYLOAD_INVALID", "请求格式无效。", traceId(request));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<MemoryWriteErrorResponse> handleUnexpected(Exception exception,
                                                                    HttpServletRequest request) {
        MemoryWriteErrorResponse body = new MemoryWriteErrorResponse(
                "MEMORY_INTERNAL_ERROR", "Memory write 处理失败。", traceId(request));
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    private String traceId(HttpServletRequest request) {
        Object value = request.getAttribute("traceId");
        return value == null ? "unknown" : value.toString();
    }
}
