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

    /** Binds the immutable durable external wait; same value is an idempotent replay. */
    void bindExternalWait(String expenseId, String waitId);

    /** Binds provider correlation and performs the B1 SUBMITTED -> WAITING_APPROVAL transition. */
    void bindExternalRequest(String expenseId, String provider, String externalRequestId);

    /** Applies only an authoritative terminal OA status; same-terminal replay is a no-op. */
    void applyExternalApprovalStatus(String externalRequestId, ExpenseStatus status);

    /** Bounded retry candidates only; this is deliberately not approval-status polling. */
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
