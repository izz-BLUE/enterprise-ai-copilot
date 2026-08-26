package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.ExpenseListResponse;
import com.fantuan.copilot.dto.action.ExpenseStatusResponse;
import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 只读服务：供 Python → Java 内部 Tool 接口（/api/internal/expense/*）调用。
 *
 * employeeId 由可信上游（LangGraphAgentController）注入，本服务只做严格按员工查询：
 * - 按 expense_id 查询时校验 expense.employeeId == 可信 employeeId（V2 §二十四
 *   ownership），否则不能跨员工读取；
 * - 最近几笔只按员工查询。
 */
@Service
public class ExpenseReadService {

    private static final int DEFAULT_LIMIT = 10;
    private static final int MAX_LIMIT = 50;

    private final ExpenseClaimRepository claims;

    public ExpenseReadService(ExpenseClaimRepository claims) {
        this.claims = claims;
    }

    public ExpenseStatusResponse getStatus(String employeeId, String expenseId) {
        requireEmployeeId(employeeId);
        if (expenseId == null || expenseId.isBlank()) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "EXPENSE_ID_REQUIRED",
                    "缺少 expense_id。", null, null);
        }
        ExpenseClaim claim = claims.findByExpenseId(expenseId).orElseThrow(() ->
                new ActionException(HttpStatus.NOT_FOUND, "EXPENSE_NOT_FOUND",
                        "未找到报销单。", null, null));
        if (!claim.employeeId().equals(employeeId)) {
            throw new ActionException(HttpStatus.NOT_FOUND, "EXPENSE_NOT_FOUND",
                    "未找到报销单。", null, null);
        }
        return toStatus(claim);
    }

    public ExpenseListResponse listRecent(String employeeId, Integer limit) {
        requireEmployeeId(employeeId);
        int boundedLimit = clampLimit(limit);
        List<ExpenseClaim> rows = claims.findRecentByEmployee(employeeId, boundedLimit);
        return new ExpenseListResponse(employeeId, rows.size(),
                rows.stream().map(this::toStatus).toList());
    }

    private ExpenseStatusResponse toStatus(ExpenseClaim claim) {
        return new ExpenseStatusResponse(
                claim.expenseId(), claim.status(), claim.claimedAmount(),
                claim.reimbursableAmount(), claim.tripId(), claim.createdAt());
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
