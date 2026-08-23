package com.fantuan.copilot.controller.admin;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.dto.auth.AuthErrorResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 管理员日志查询接口。
 *
 * 权限由 SecurityConfig 的 /api/admin/** -> hasRole("ADMIN") 把关；
 * 本类不再做角色判断，避免在前后端之间重复实现权限边界。
 */
@RestController
@RequestMapping("/api/admin")
public class AdminLogController {

    private final AdminLogBuffer buffer;

    public AdminLogController(AdminLogBuffer buffer) {
        this.buffer = buffer;
    }

    @GetMapping(value = "/logs", produces = "application/json")
    public ResponseEntity<Map<String, Object>> logs(
            @RequestParam(required = false) String level,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String traceId,
            @RequestParam(required = false) Integer limit) {
        List<AdminLogEvent> items = buffer.snapshot(level, category, traceId, limit);
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(Map.of(
                        "items", items,
                        "count", items.size()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<AuthErrorResponse> handleBadParam(IllegalArgumentException ex,
                                                            HttpServletRequest request) {
        Object traceIdAttr = request.getAttribute("traceId");
        String traceId = traceIdAttr == null ? "unknown" : traceIdAttr.toString();
        return ResponseEntity.badRequest()
                .cacheControl(CacheControl.noStore())
                .body(new AuthErrorResponse("BAD_ADMIN_LOG_FILTER", ex.getMessage(), traceId));
    }
}