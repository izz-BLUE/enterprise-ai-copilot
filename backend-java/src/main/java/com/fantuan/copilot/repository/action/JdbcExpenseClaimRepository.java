package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.ExpenseStatus;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Repository
public class JdbcExpenseClaimRepository implements ExpenseClaimRepository {
    private final NamedParameterJdbcTemplate jdbc;

    public JdbcExpenseClaimRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public long nextNumber() {
        Long value = jdbc.queryForObject("SELECT nextval('expense_claim_number_seq')",
                Map.of(), Long.class);
        if (value == null) {
            throw new IllegalStateException("Expense claim sequence unavailable");
        }
        return value;
    }

    @Override
    public void save(String sourceActionId, ExpenseClaim claim, List<ExpenseItem> items) {
        jdbc.update("""
                INSERT INTO expense_claim(expense_id, source_action_id, employee_id, trip_id,
                    cost_center, claimed_amount, reimbursable_amount, status, created_at, updated_at)
                VALUES (:expenseId, :sourceActionId, :employeeId, :tripId,
                    :costCenter, :claimedAmount, :reimbursableAmount, :status, :createdAt, :updatedAt)
                """, Map.of(
                "expenseId", claim.expenseId(),
                "sourceActionId", sourceActionId,
                "employeeId", claim.employeeId(),
                "tripId", claim.tripId(),
                "costCenter", claim.costCenter(),
                "claimedAmount", claim.claimedAmount(),
                "reimbursableAmount", claim.reimbursableAmount(),
                "status", claim.status().name(),
                "createdAt", Timestamp.from(claim.createdAt()),
                "updatedAt", Timestamp.from(claim.updatedAt())));
        items.forEach(item -> jdbc.update("""
                INSERT INTO expense_item(expense_id, invoice_id, category, amount, description)
                VALUES (:expenseId, :invoiceId, :category, :amount, :description)
                """, Map.of(
                "expenseId", claim.expenseId(),
                "invoiceId", item.invoiceId(),
                "category", item.category(),
                "amount", item.amount(),
                "description", item.description())));
    }

    @Override
    public int countBySourceActionId(String sourceActionId) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM expense_claim "
                + "WHERE source_action_id = :id", Map.of("id", sourceActionId), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public Optional<ExpenseClaim> findByExpenseId(String expenseId) {
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim WHERE expense_id = :id
                """, Map.of("id", expenseId), (rs, rowNum) -> new ExpenseClaim(
                rs.getString("expense_id"),
                rs.getString("source_action_id"),
                rs.getString("employee_id"),
                rs.getString("trip_id"),
                rs.getString("cost_center"),
                rs.getBigDecimal("claimed_amount"),
                rs.getBigDecimal("reimbursable_amount"),
                ExpenseStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("updated_at").toInstant(),
                rs.getString("external_provider"), rs.getString("external_request_id"),
                rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                nullableInstant(rs, "external_resume_last_attempt_at"),
                nullableInstant(rs, "external_resume_completed_at")))
                .stream().findFirst();
    }

    @Override
    public Optional<ExpenseClaim> findByExternalRequestId(String requestId) {
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim WHERE external_request_id = :requestId
                """, Map.of("requestId", requestId), (rs, rowNum) -> new ExpenseClaim(
                rs.getString("expense_id"),
                rs.getString("source_action_id"),
                rs.getString("employee_id"),
                rs.getString("trip_id"),
                rs.getString("cost_center"),
                rs.getBigDecimal("claimed_amount"),
                rs.getBigDecimal("reimbursable_amount"),
                ExpenseStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("updated_at").toInstant(),
                rs.getString("external_provider"), rs.getString("external_request_id"),
                rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                nullableInstant(rs, "external_resume_last_attempt_at"),
                nullableInstant(rs, "external_resume_completed_at")))
                .stream().findFirst();
    }

    @Override
    public void bindExternalWait(String expenseId, String waitId) {
        int changed = jdbc.update("""
                UPDATE expense_claim
                SET external_wait_id = :waitId, updated_at = :now
                WHERE expense_id = :expenseId
                  AND (external_wait_id IS NULL OR external_wait_id = :waitId)
                """, Map.of("expenseId", expenseId, "waitId", waitId,
                "now", Timestamp.from(Instant.now())));
        if (changed != 1) {
            throw new IllegalStateException("External wait correlation conflict");
        }
    }

    @Override
    public void bindExternalRequest(String expenseId, String provider, String externalRequestId) {
        int changed = jdbc.update("""
                UPDATE expense_claim
                SET external_provider = :provider, external_request_id = :requestId,
                    status = 'WAITING_APPROVAL', updated_at = :now
                WHERE expense_id = :expenseId
                  AND ((external_request_id IS NULL AND status = 'SUBMITTED')
                       OR (external_request_id = :requestId AND external_provider = :provider
                           AND status = 'WAITING_APPROVAL'))
                """, Map.of("expenseId", expenseId, "provider", provider,
                "requestId", externalRequestId, "now", Timestamp.from(Instant.now())));
        if (changed != 1) {
            throw new IllegalStateException("External request correlation conflict");
        }
    }

    @Override
    public void applyExternalApprovalStatus(String externalRequestId, ExpenseStatus status) {
        if (status != ExpenseStatus.APPROVED && status != ExpenseStatus.REJECTED) {
            throw new IllegalArgumentException("Only terminal external approval statuses are supported");
        }
        int changed = jdbc.update("""
                UPDATE expense_claim
                SET status = :status, updated_at = :now
                WHERE external_request_id = :requestId AND status = 'WAITING_APPROVAL'
                """, Map.of("requestId", externalRequestId, "status", status.name(),
                "now", Timestamp.from(Instant.now())));
        if (changed == 1) {
            return;
        }
        ExpenseStatus current = jdbc.query("""
                SELECT status FROM expense_claim WHERE external_request_id = :requestId
                """, Map.of("requestId", externalRequestId),
                (rs, rowNum) -> ExpenseStatus.valueOf(rs.getString("status")))
                .stream().findFirst().orElseThrow(
                        () -> new IllegalStateException("External approval request not found"));
        if (current != status) {
            throw new IllegalStateException("External approval status transition conflict");
        }
    }

    @Override
    public List<ExpenseClaim> findExternalApprovalReconciliationCandidates(Instant cutoff, int limit) {
        int boundedLimit = Math.max(1, Math.min(limit, 100));
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim
                WHERE status = 'WAITING_APPROVAL'
                  AND external_provider = 'MOCK_OA'
                  AND external_request_id IS NOT NULL
                  AND (external_last_checked_at IS NULL OR external_last_checked_at <= :cutoff)
                ORDER BY external_last_checked_at ASC NULLS FIRST, expense_id ASC
                LIMIT :limit
                """, Map.of("cutoff", Timestamp.from(cutoff), "limit", boundedLimit),
                (rs, rowNum) -> new ExpenseClaim(
                rs.getString("expense_id"), rs.getString("source_action_id"),
                rs.getString("employee_id"), rs.getString("trip_id"),
                rs.getString("cost_center"), rs.getBigDecimal("claimed_amount"),
                rs.getBigDecimal("reimbursable_amount"), ExpenseStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                rs.getString("external_provider"), rs.getString("external_request_id"),
                rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                nullableInstant(rs, "external_resume_last_attempt_at"),
                nullableInstant(rs, "external_resume_completed_at")));
    }

    @Override
    public boolean tryMarkExternalApprovalChecked(String expenseId, String externalRequestId,
                                                   Instant cutoff, Instant checkedAt) {
        return jdbc.update("""
                UPDATE expense_claim
                SET external_last_checked_at = :checkedAt
                WHERE expense_id = :expenseId
                  AND external_request_id = :requestId
                  AND external_provider = 'MOCK_OA'
                  AND status = 'WAITING_APPROVAL'
                  AND (external_last_checked_at IS NULL OR external_last_checked_at <= :cutoff)
                """, Map.of("expenseId", expenseId, "requestId", externalRequestId,
                "cutoff", Timestamp.from(cutoff), "checkedAt", Timestamp.from(checkedAt))) == 1;
    }

    @Override
    public List<ExpenseClaim> findExternalResumeCandidates(Instant cutoff, int limit) {
        int boundedLimit = Math.max(1, Math.min(limit, 100));
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim
                WHERE status IN ('APPROVED', 'REJECTED')
                  AND external_provider = 'MOCK_OA'
                  AND external_request_id IS NOT NULL
                  AND external_wait_id IS NOT NULL
                  AND external_resume_completed_at IS NULL
                  AND (external_resume_last_attempt_at IS NULL
                       OR external_resume_last_attempt_at <= :cutoff)
                ORDER BY external_resume_last_attempt_at ASC NULLS FIRST, expense_id ASC
                LIMIT :limit
                """, Map.of("cutoff", Timestamp.from(cutoff), "limit", boundedLimit),
                (rs, rowNum) -> mapClaim(rs));
    }

    @Override
    public boolean tryMarkExternalResumeAttempt(String expenseId, Instant cutoff,
                                                 Instant attemptedAt) {
        return jdbc.update("""
                UPDATE expense_claim
                SET external_resume_last_attempt_at = :attemptedAt
                WHERE expense_id = :expenseId
                  AND status IN ('APPROVED', 'REJECTED')
                  AND external_provider = 'MOCK_OA'
                  AND external_request_id IS NOT NULL
                  AND external_wait_id IS NOT NULL
                  AND external_resume_completed_at IS NULL
                  AND (external_resume_last_attempt_at IS NULL
                       OR external_resume_last_attempt_at <= :cutoff)
                """, Map.of("expenseId", expenseId, "cutoff", Timestamp.from(cutoff),
                "attemptedAt", Timestamp.from(attemptedAt))) == 1;
    }

    @Override
    public void markExternalResumeCompleted(String expenseId, Instant completedAt) {
        jdbc.update("""
                UPDATE expense_claim
                SET external_resume_completed_at = COALESCE(external_resume_completed_at, :completedAt)
                WHERE expense_id = :expenseId
                  AND status IN ('APPROVED', 'REJECTED')
                  AND external_resume_completed_at IS NULL
                """, Map.of("expenseId", expenseId, "completedAt", Timestamp.from(completedAt)));
    }

    @Override
    public List<ExpenseClaim> findPendingExternalSubmissions(int limit) {
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim
                WHERE status = 'SUBMITTED' AND external_wait_id IS NOT NULL
                  AND external_request_id IS NULL
                ORDER BY updated_at ASC
                LIMIT :limit
                """, Map.of("limit", limit), (rs, rowNum) -> new ExpenseClaim(
                rs.getString("expense_id"), rs.getString("source_action_id"),
                rs.getString("employee_id"), rs.getString("trip_id"),
                rs.getString("cost_center"), rs.getBigDecimal("claimed_amount"),
                rs.getBigDecimal("reimbursable_amount"), ExpenseStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                rs.getString("external_provider"), rs.getString("external_request_id"),
                rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                nullableInstant(rs, "external_resume_last_attempt_at"),
                nullableInstant(rs, "external_resume_completed_at")));
    }

    @Override
    public List<ExpenseItem> findItemsByExpenseId(String expenseId) {
        return jdbc.query("""
                SELECT invoice_id, category, amount, description
                FROM expense_item WHERE expense_id = :expenseId
                ORDER BY item_id
                """, Map.of("expenseId", expenseId),
                (rs, rowNum) -> new ExpenseItem(
                        rs.getString("invoice_id"),
                        rs.getString("category"),
                        rs.getBigDecimal("amount"),
                        rs.getString("description")));
    }

    @Override
    public List<ExpenseClaim> findRecentByEmployee(String employeeId, int limit) {
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id,
                       external_last_checked_at, external_resume_last_attempt_at,
                       external_resume_completed_at
                FROM expense_claim
                WHERE employee_id = :employeeId
                ORDER BY created_at DESC
                LIMIT :limit
                """, Map.of("employeeId", employeeId, "limit", limit),
                (rs, rowNum) -> new ExpenseClaim(
                        rs.getString("expense_id"),
                        rs.getString("source_action_id"),
                        rs.getString("employee_id"),
                        rs.getString("trip_id"),
                        rs.getString("cost_center"),
                        rs.getBigDecimal("claimed_amount"),
                        rs.getBigDecimal("reimbursable_amount"),
                        ExpenseStatus.valueOf(rs.getString("status")),
                        rs.getTimestamp("created_at").toInstant(),
                        rs.getTimestamp("updated_at").toInstant(),
                        rs.getString("external_provider"), rs.getString("external_request_id"),
                        rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                        nullableInstant(rs, "external_resume_last_attempt_at"),
                        nullableInstant(rs, "external_resume_completed_at")));
    }

    private Instant nullableInstant(ResultSet rs, String column) throws SQLException {
        Timestamp timestamp = rs.getTimestamp(column);
        return timestamp == null ? null : timestamp.toInstant();
    }

    private ExpenseClaim mapClaim(ResultSet rs) throws SQLException {
        return new ExpenseClaim(
                rs.getString("expense_id"), rs.getString("source_action_id"),
                rs.getString("employee_id"), rs.getString("trip_id"),
                rs.getString("cost_center"), rs.getBigDecimal("claimed_amount"),
                rs.getBigDecimal("reimbursable_amount"), ExpenseStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                rs.getString("external_provider"), rs.getString("external_request_id"),
                rs.getString("external_wait_id"), nullableInstant(rs, "external_last_checked_at"),
                nullableInstant(rs, "external_resume_last_attempt_at"),
                nullableInstant(rs, "external_resume_completed_at"));
    }
}
