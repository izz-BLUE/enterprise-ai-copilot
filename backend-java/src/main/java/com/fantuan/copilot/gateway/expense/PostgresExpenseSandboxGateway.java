package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

/**
 * 报销 sandbox 网关：与 PendingAction 同一本地 PostgreSQL 事务内
 * 写入 expense_claim + expense_item（V2 §二十三）。
 *
 * 编号：EXP-YYYYMMDD-NNNNNN（YYYYMMDD 为提交日期，NNNNNN 为 sequence）。
 * 状态：SUBMITTED（V2 §二十二：本轮唯一真实持久化状态）。
 *
 * 幂等：expense_claim.source_action_id UNIQUE —— 同一确认动作不会重复创建。
 */
@Component
public class PostgresExpenseSandboxGateway implements ExpenseExecutionGateway {
    private static final DateTimeFormatter EXPENSE_DATE = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final ExpenseClaimRepository claims;
    private final BusinessActionProperties properties;

    public PostgresExpenseSandboxGateway(ExpenseClaimRepository claims,
                                         BusinessActionProperties properties) {
        this.claims = claims;
        this.properties = properties;
    }

    @Override
    public ExpenseExecutionResult submit(ExpenseSubmission submission) {
        long number = claims.nextNumber();
        ZoneId zone = properties.zoneId();
        LocalDate day = submission.submittedAt().atZone(zone).toLocalDate();
        String expenseId = "EXP-" + day.format(EXPENSE_DATE) + "-"
                + String.format("%06d", number);
        ExpenseClaim claim = new ExpenseClaim(
                expenseId, submission.sourceActionId(), submission.employeeId(),
                submission.tripId(), submission.costCenter(),
                submission.claimedAmount(), submission.reimbursableAmount(),
                ExpenseStatus.SUBMITTED, submission.submittedAt(), submission.submittedAt());
        claims.save(submission.sourceActionId(), claim, submission.items());
        return new ExpenseExecutionResult(expenseId, submission.submittedAt());
    }
}
