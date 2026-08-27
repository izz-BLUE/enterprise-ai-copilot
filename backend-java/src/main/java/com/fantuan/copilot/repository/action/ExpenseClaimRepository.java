package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseItem;

import java.util.List;
import java.util.Optional;

public interface ExpenseClaimRepository {
    long nextNumber();

    /** 在同一事务内写入 claim + items；source_action_id UNIQUE 不允许重复。 */
    void save(String sourceActionId, ExpenseClaim claim, List<ExpenseItem> items);

    int countBySourceActionId(String sourceActionId);

    Optional<ExpenseClaim> findByExpenseId(String expenseId);

    /** Binds the immutable durable external wait; same value is an idempotent replay. */
    void bindExternalWait(String expenseId, String waitId);

    /** Binds provider correlation and performs the B1 SUBMITTED -> WAITING_APPROVAL transition. */
    void bindExternalRequest(String expenseId, String provider, String externalRequestId);

    /** Bounded retry candidates only; this is deliberately not approval-status polling. */
    List<ExpenseClaim> findPendingExternalSubmissions(int limit);

    List<ExpenseItem> findItemsByExpenseId(String expenseId);

    /** 按 employee_id 倒序拉取最近报销单（只读企业 Tool 用）。 */
    List<ExpenseClaim> findRecentByEmployee(String employeeId, int limit);
}
