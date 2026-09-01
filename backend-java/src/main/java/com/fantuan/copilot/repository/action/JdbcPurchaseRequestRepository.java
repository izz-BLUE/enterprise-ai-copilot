package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.PurchaseRequest;
import com.fantuan.copilot.model.action.PurchaseRequestStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.util.Map;
import java.util.Optional;

@Repository
public class JdbcPurchaseRequestRepository implements PurchaseRequestRepository {
    private final NamedParameterJdbcTemplate jdbc;

    public JdbcPurchaseRequestRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public long nextNumber() {
        Long value = jdbc.queryForObject("SELECT nextval('purchase_request_number_seq')",
                Map.of(), Long.class);
        if (value == null) {
            throw new IllegalStateException("Purchase request sequence unavailable");
        }
        return value;
    }

    @Override
    public void save(PurchaseRequest request) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("requestId", request.requestId())
                .addValue("sourceActionId", request.sourceActionId())
                .addValue("ownerUserId", request.ownerUserId())
                .addValue("employeeId", request.employeeId())
                .addValue("itemName", request.itemName())
                .addValue("requestedBudget", request.requestedBudget())
                .addValue("justification", request.justification())
                .addValue("status", request.status().name())
                .addValue("createdAt", Timestamp.from(request.createdAt()));
        jdbc.update("""
                INSERT INTO purchase_request(request_id, source_action_id, owner_user_id,
                    employee_id, item_name, requested_budget, justification, status, created_at)
                VALUES (:requestId, :sourceActionId, :ownerUserId, :employeeId,
                    :itemName, :requestedBudget, :justification, :status, :createdAt)
                """, parameters);
    }

    @Override
    public int countBySourceActionId(String sourceActionId) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM purchase_request "
                + "WHERE source_action_id = :id", Map.of("id", sourceActionId), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public int size() {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM purchase_request", Map.of(), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public Optional<PurchaseRequest> findByRequestId(String requestId) {
        return jdbc.query("""
                SELECT request_id, source_action_id, owner_user_id, employee_id, item_name,
                       requested_budget, justification, status, created_at
                FROM purchase_request WHERE request_id = :requestId
                """, Map.of("requestId", requestId), (rs, rowNum) -> new PurchaseRequest(
                rs.getString("request_id"), rs.getString("source_action_id"),
                rs.getString("owner_user_id"), rs.getString("employee_id"),
                rs.getString("item_name"), rs.getBigDecimal("requested_budget"),
                rs.getString("justification"),
                PurchaseRequestStatus.valueOf(rs.getString("status")),
                rs.getTimestamp("created_at").toInstant())).stream().findFirst();
    }
}
