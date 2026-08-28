package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.LeaveBalanceResponse;
import com.fantuan.copilot.dto.action.LeaveRequestListResponse;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.LeaveReadService;
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
 * 只读内部接口：供 Python Agent (Python→Java 内部客户端) 调用。
 * 鉴权仅依赖 X-Internal-Token 与调用方在已认证请求链路上游解析后注入的 X-Employee-Id；
 * 身份解析发生在外部
 * 请求进入 Java 的第一跳(LangGraphAgentController)，内部接口仅消费可信 employeeId。
 */
@RestController
@RequestMapping("/api/internal/leave")
public class LeaveReadController {

    private static final Logger log = LoggerFactory.getLogger(LeaveReadController.class);

    private final LeaveReadService service;

    @Value("${leave.read.internal-token:}")
    private String expectedInternalToken;

    public LeaveReadController(LeaveReadService service) {
        this.service = service;
    }

    @GetMapping("/balance")
    public ResponseEntity<LeaveBalanceResponse> balance(
            @RequestHeader(value = "X-Internal-Token", required = false) String internalToken,
            @RequestHeader(value = "X-Employee-Id", required = false) String employeeId,
            HttpServletRequest httpRequest) {
        requireInternalToken(internalToken);
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] leave_balance_tool 内部调用 employeeId={}", traceId, employeeId);
        return noStore(service.getBalance(employeeId));
    }

    @GetMapping("/requests")
    public ResponseEntity<LeaveRequestListResponse> requests(
            @RequestHeader(value = "X-Internal-Token", required = false) String internalToken,
            @RequestHeader(value = "X-Employee-Id", required = false) String employeeId,
            @RequestParam(value = "limit", required = false) Integer limit,
            HttpServletRequest httpRequest) {
        requireInternalToken(internalToken);
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] leave_request_tool 内部调用 employeeId={} limit={}",
                traceId, employeeId, limit);
        return noStore(service.listRequests(employeeId, limit));
    }

    private void requireInternalToken(String presented) {
        if (expectedInternalToken == null || expectedInternalToken.isBlank()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE,
                    "LEAVE_READ_DISABLED", "内部只读接口未启用。", null, null);
        }
        if (presented == null || !presented.equals(expectedInternalToken)) {
            throw new ActionException(HttpStatus.FORBIDDEN,
                    "LEAVE_READ_FORBIDDEN", "内部只读接口鉴权失败。", null, null);
        }
    }

    private <T> ResponseEntity<T> noStore(T body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }
}
