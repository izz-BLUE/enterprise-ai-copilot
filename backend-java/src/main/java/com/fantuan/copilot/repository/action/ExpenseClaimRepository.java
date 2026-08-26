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

    List<ExpenseItem> findItemsByExpenseId(String expenseId);

    /** 按 employee_id 倒序拉取最近报销单（只读企业 Tool 用）。 */
    List<ExpenseClaim> findRecentByEmployee(String employeeId, int limit);
}
