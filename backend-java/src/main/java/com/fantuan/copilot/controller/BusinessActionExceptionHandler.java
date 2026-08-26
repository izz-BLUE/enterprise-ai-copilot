package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.ActionErrorResponse;
import com.fantuan.copilot.service.action.ActionException;
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
@RestControllerAdvice(assignableTypes = {
        BusinessActionController.class,
        DemoIdentityController.class,
        LeaveReadController.class,
        ExpenseReadController.class
})
public class BusinessActionExceptionHandler {

    @ExceptionHandler(ActionException.class)
    public ResponseEntity<ActionErrorResponse> handleAction(ActionException exception,
                                                            HttpServletRequest request) {
        ActionErrorResponse body = new ActionErrorResponse(exception.actionId(),
                exception.actionStatus(), exception.errorCode(), exception.getMessage(), traceId(request));
        return ResponseEntity.status(exception.httpStatus())
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, HttpMessageNotReadableException.class})
    public ResponseEntity<ActionErrorResponse> handleInvalidRequest(Exception exception,
                                                                   HttpServletRequest request) {
        ActionErrorResponse body = new ActionErrorResponse(null, null,
                "INVALID_REQUEST", "请求格式无效。", traceId(request));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ActionErrorResponse> handleUnexpected(Exception exception,
                                                                HttpServletRequest request) {
        ActionErrorResponse body = new ActionErrorResponse(null, null,
                "ACTION_INTERNAL_ERROR", "业务动作处理失败。", traceId(request));
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    private String traceId(HttpServletRequest request) {
        Object value = request.getAttribute("traceId");
        return value == null ? "unknown" : value.toString();
    }
}
