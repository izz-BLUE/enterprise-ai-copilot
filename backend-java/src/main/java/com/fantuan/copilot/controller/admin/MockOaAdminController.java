package com.fantuan.copilot.controller.admin;

import com.fantuan.copilot.dto.admin.MockOaApprovalActionResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalListResponse;
import com.fantuan.copilot.dto.auth.AuthErrorResponse;
import com.fantuan.copilot.gateway.expense.MockOaAdminException;
import com.fantuan.copilot.gateway.expense.MockOaAdminGateway;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 管理员模拟 OA 审批台接口，浏览器只通过 Java 访问。 */
@RestController
@RequestMapping("/api/admin/mock-oa/expense-approvals")
public class MockOaAdminController {
    private static final Logger log = LoggerFactory.getLogger(MockOaAdminController.class);

    private final MockOaAdminGateway gateway;

    public MockOaAdminController(MockOaAdminGateway gateway) {
        this.gateway = gateway;
    }

    @GetMapping
    public ResponseEntity<MockOaApprovalListResponse> list(
            @RequestParam(required = false) String status) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(gateway.list(status));
    }

    @PostMapping("/{requestId}/approve")
    public ResponseEntity<MockOaApprovalActionResponse> approve(@PathVariable String requestId) {
        return decide(requestId, "APPROVED");
    }

    @PostMapping("/{requestId}/reject")
    public ResponseEntity<MockOaApprovalActionResponse> reject(@PathVariable String requestId) {
        return decide(requestId, "REJECTED");
    }

    private ResponseEntity<MockOaApprovalActionResponse> decide(String requestId, String decision) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(gateway.decide(requestId, decision));
    }

    @ExceptionHandler(MockOaAdminException.class)
    public ResponseEntity<AuthErrorResponse> handleGatewayFailure(
            MockOaAdminException exception, HttpServletRequest request) {
        return ResponseEntity.status(exception.httpStatus())
                .cacheControl(CacheControl.noStore())
                .body(new AuthErrorResponse(exception.errorCode(), exception.getMessage(), traceId(request)));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<AuthErrorResponse> handleUnexpected(
            Exception exception, HttpServletRequest request) {
        log.error("模拟 OA 管理接口失败 errorType={}", exception.getClass().getSimpleName());
        return ResponseEntity.status(502)
                .cacheControl(CacheControl.noStore())
                .body(new AuthErrorResponse("MOCK_OA_REQUEST_FAILED",
                        "模拟 OA 请求未完成，请稍后重试。", traceId(request)));
    }

    private String traceId(HttpServletRequest request) {
        Object value = request.getAttribute("traceId");
        return value == null ? "unknown" : value.toString();
    }
}
