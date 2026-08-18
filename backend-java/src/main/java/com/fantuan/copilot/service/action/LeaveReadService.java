package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.LeaveBalanceResponse;
import com.fantuan.copilot.dto.action.LeaveRequestListResponse;
import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

/**
 * 只读服务：供 Python → Java 内部 Tool 接口（/api/internal/leave/*）调用。
 * employeeId 由可信上游（LangGraphAgentController）注入，本服务只做严格按员工查询：
 * 不接受任意员工查询能力，不修改任何写路径。
 */
@Service
public class LeaveReadService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 50;

    private final LeaveAccountRepository accounts;
    private final LeaveRequestRepository requests;

    public LeaveReadService(LeaveAccountRepository accounts, LeaveRequestRepository requests) {
        this.accounts = accounts;
        this.requests = requests;
    }

    public LeaveBalanceResponse getBalance(String employeeId) {
        requireEmployeeId(employeeId);
        BigDecimal balance = accounts.findBalance(employeeId).orElseThrow(() ->
                new ActionException(HttpStatus.NOT_FOUND, "LEAVE_ACCOUNT_NOT_FOUND",
                        "未找到假期账户。", null, null));
        return new LeaveBalanceResponse(employeeId, balance, Instant.now());
    }

    public LeaveRequestListResponse listRequests(String employeeId, Integer limit) {
        requireEmployeeId(employeeId);
        int boundedLimit = clampLimit(limit);
        List<LeaveRequest> rows = requests.findRecentByEmployee(employeeId, boundedLimit);
        return LeaveRequestListResponse.of(employeeId, rows);
    }

    private void requireEmployeeId(String employeeId) {
        if (employeeId == null || employeeId.isBlank()) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "EMPLOYEE_ID_REQUIRED",
                    "缺少员工身份。", null, null);
        }
    }

    private int clampLimit(Integer requested) {
        if (requested == null || requested <= 0) {
            return DEFAULT_LIMIT;
        }
        return Math.min(requested, MAX_LIMIT);
    }
}