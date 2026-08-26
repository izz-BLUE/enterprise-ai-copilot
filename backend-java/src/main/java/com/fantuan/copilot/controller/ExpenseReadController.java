package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.ExpenseListResponse;
import com.fantuan.copilot.dto.action.ExpenseStatusResponse;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.ExpenseReadService;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 只读内部接口：供 Python Agent 的 expense_status_tool 调用（V2 §二十四）。
 *
 * 鉴权：X-Internal-Token + 可信上游注入的 X-Employee-Id；
 * ownership check 在 ExpenseReadService（expense.employeeId == 可信 employeeId）。
 */
@RestController
@RequestMapping("/api/internal/expense")
public class ExpenseReadController {

    private static final Logger log = LoggerFactory.getLogger(ExpenseReadController.class);

    private final ExpenseReadService service;

    @Value("${expense.read.internal-token:${leave.read.internal-token:}}")
    private String expectedInternalToken;

    public ExpenseReadController(ExpenseReadService service) {
        this.service = service;
    }

    @GetMapping("/status")
    public ResponseEntity<ExpenseStatusResponse> status(
            @RequestHeader(value = "X-Internal-Token", required = false) String internalToken,
            @RequestHeader(value = "X-Employee-Id", required = false) String employeeId,
            @RequestParam(value = "expenseId", required = false) String expenseId,
            HttpServletRequest httpRequest) {
        requireInternalToken(internalToken);
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] expense_status_tool 内部调用 employeeId={} expenseId={}",
                traceId, employeeId, expenseId);
        return noStore(service.getStatus(employeeId, expenseId));
    }

    @GetMapping("/recent")
    public ResponseEntity<ExpenseListResponse> recent(
            @RequestHeader(value = "X-Internal-Token", required = false) String internalToken,
            @RequestHeader(value = "X-Employee-Id", required = false) String employeeId,
            @RequestParam(value = "limit", required = false) Integer limit,
            HttpServletRequest httpRequest) {
        requireInternalToken(internalToken);
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] expense_recent_tool 内部调用 employeeId={} limit={}",
                traceId, employeeId, limit);
        return noStore(service.listRecent(employeeId, limit));
    }

    private void requireInternalToken(String presented) {
        if (expectedInternalToken == null || expectedInternalToken.isBlank()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "EXPENSE_READ_DISABLED", "内部只读接口未启用。", null, null);
        }
        if (presented == null || !presented.equals(expectedInternalToken)) {
            throw new ActionException(HttpStatus.FORBIDDEN,
                    "EXPENSE_READ_FORBIDDEN", "内部只读接口鉴权失败。", null, null);
        }
    }

    private <T> ResponseEntity<T> noStore(T body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }
}
