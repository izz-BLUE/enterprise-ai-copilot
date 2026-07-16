package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.LeaveRequest;
import com.fantuan.copilot.model.action.PendingAction;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.YearMonth;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

@Service
public class LeaveSandboxService {
    private final Clock clock;
    private final int maxCompleted;
    private BigDecimal balance;
    private final List<LeaveRequest> requests = new ArrayList<>();
    private int sequence;

    public LeaveSandboxService(BusinessActionProperties properties, Clock clock) {
        this.clock = clock;
        this.balance = properties.getDemoAnnualLeaveBalance();
        this.maxCompleted = properties.getMaxCompleted();
    }

    public synchronized Preview preview(String employeeId, java.time.LocalDate start,
                                        java.time.LocalDate end, BigDecimal days) {
        if (hasConflict(employeeId, start, end)) {
            throw rule("日期范围与已提交的模拟申请冲突。");
        }
        if (balance.compareTo(days) < 0) {
            throw rule("模拟年假余额不足。");
        }
        return new Preview(balance, balance.subtract(days));
    }

    public synchronized LeaveRequest submit(PendingAction action) {
        if (balance.compareTo(action.balanceBefore()) != 0
                || balance.compareTo(action.days()) < 0
                || hasConflict(action.employeeId(), action.startDate(), action.endDate())) {
            throw new ActionException(HttpStatus.CONFLICT, "ACTION_STALE",
                    "申请状态已变化，请重新生成草稿。", action.actionId(), action.status());
        }
        balance = balance.subtract(action.days());
        sequence++;
        String requestId = "LR-" + YearMonth.now(clock).format(DateTimeFormatter.ofPattern("yyyyMM"))
                + "-" + String.format("%04d", sequence);
        LeaveRequest request = new LeaveRequest(requestId, action.employeeId(), "ANNUAL",
                action.startDate(), action.endDate(), action.halfDay(), action.days(), clock.instant());
        requests.add(request);
        while (requests.size() > maxCompleted) {
            requests.remove(0);
        }
        return request;
    }

    public synchronized BigDecimal balance() { return balance; }
    public synchronized List<LeaveRequest> requests() { return List.copyOf(requests); }

    private boolean hasConflict(String employeeId, java.time.LocalDate start, java.time.LocalDate end) {
        return requests.stream().anyMatch(request -> request.employeeId().equals(employeeId)
                && !end.isBefore(request.startDate()) && !start.isAfter(request.endDate()));
    }

    private ActionException rule(String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", message, null, null);
    }

    public record Preview(BigDecimal balanceBefore, BigDecimal balanceAfter) {}
}
