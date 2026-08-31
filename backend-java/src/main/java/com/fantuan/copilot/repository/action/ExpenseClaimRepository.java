package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.ExpenseStatus;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface ExpenseClaimRepository {
    long nextNumber();

    /** 在同一事务内写入 claim + items；source_action_id UNIQUE 不允许重复。 */
    void save(String sourceActionId, ExpenseClaim claim, List<ExpenseItem> items);

    int countBySourceActionId(String sourceActionId);

    Optional<ExpenseClaim> findByExpenseId(String expenseId);

    Optional<ExpenseClaim> findByExternalRequestId(String requestId);

    /** 绑定不可变的持久化外部等待；相同值视为幂等重放。 */
    void bindExternalWait(String expenseId, String waitId);

    /** 绑定 provider 关联，并执行 B1 SUBMITTED -> WAITING_APPROVAL 转换。 */
    void bindExternalRequest(String expenseId, String provider, String externalRequestId);

    /** 只应用权威的 OA 终态 status；相同终态重放时为 no-op。 */
    void applyExternalApprovalStatus(String externalRequestId, ExpenseStatus status);

    /** 仅返回有界重试候选；有意不执行审批 status 轮询。 */
    List<ExpenseClaim> findPendingExternalSubmissions(int limit);

    List<ExpenseClaim> findExternalApprovalReconciliationCandidates(Instant cutoff, int limit);

    boolean tryMarkExternalApprovalChecked(String expenseId, String externalRequestId,
                                           Instant cutoff, Instant checkedAt);

    List<ExpenseClaim> findExternalResumeCandidates(Instant cutoff, int limit);

    boolean tryMarkExternalResumeAttempt(String expenseId, Instant cutoff, Instant attemptedAt);

    void markExternalResumeCompleted(String expenseId, Instant completedAt);

    List<ExpenseItem> findItemsByExpenseId(String expenseId);

    /** 按 employee_id 倒序拉取最近报销单（只读企业 Tool 用）。 */
    List<ExpenseClaim> findRecentByEmployee(String employeeId, int limit);
}
