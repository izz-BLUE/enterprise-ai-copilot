package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.ExpenseStatus;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

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
                       external_provider, external_request_id, external_wait_id
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
                rs.getString("external_wait_id")))
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
    public List<ExpenseClaim> findPendingExternalSubmissions(int limit) {
        return jdbc.query("""
                SELECT expense_id, source_action_id, employee_id, trip_id, cost_center,
                       claimed_amount, reimbursable_amount, status, created_at, updated_at,
                       external_provider, external_request_id, external_wait_id
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
                rs.getString("external_wait_id")));
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
                       external_provider, external_request_id, external_wait_id
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
                        rs.getString("external_wait_id")));
    }
}
