package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Repository
public class JdbcPendingActionRepository implements PendingActionRepository {
    private static final String COLUMNS = """
            action_id, action_type, origin_trace_id, employee_id, display_name,
            start_date, end_date, half_day, reason, days, balance_before, balance_after,
            confirmation_nonce_digest, status, idempotency_key, request_id,
            execution_message, failure_code, created_at, expires_at, completed_at
            """;

    private final NamedParameterJdbcTemplate jdbc;
    private final RowMapper<PendingAction> rowMapper = this::map;

    public JdbcPendingActionRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void lockControl() {
        jdbc.queryForObject("SELECT control_key FROM business_action_control "
                + "WHERE control_key = 'GLOBAL' FOR UPDATE", Map.of(), String.class);
    }

    @Override
    public void saveNew(PendingAction action) {
        MapSqlParameterSource p = parameters(action);
        jdbc.update("""
                INSERT INTO business_action (
                    action_id, action_type, origin_trace_id, employee_id, display_name,
                    start_date, end_date, half_day, reason, days, balance_before, balance_after,
                    confirmation_nonce_digest, status, created_at, expires_at)
                VALUES (:actionId, :actionType, :originTraceId, :employeeId, :displayName,
                    :startDate, :endDate, :halfDay, :reason, :days, :balanceBefore, :balanceAfter,
                    :nonceDigest, :status, :createdAt, :expiresAt)
                """, p);
    }

    @Override
    public Optional<PendingAction> find(String actionId) {
        return jdbc.query("SELECT " + COLUMNS + " FROM business_action WHERE action_id = :id",
                Map.of("id", actionId), rowMapper).stream().findFirst();
    }

    @Override
    public Optional<PendingAction> findForUpdate(String actionId) {
        return jdbc.query("SELECT " + COLUMNS
                        + " FROM business_action WHERE action_id = :id FOR UPDATE",
                Map.of("id", actionId), rowMapper).stream().findFirst();
    }

    @Override
    public int countActive() {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM business_action "
                        + "WHERE status IN ('PENDING_CONFIRMATION', 'PROCESSING')",
                Map.of(), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public void markProcessing(String actionId, UUID key) {
        jdbc.update("UPDATE business_action SET status = 'PROCESSING', idempotency_key = :key "
                + "WHERE action_id = :id", Map.of("id", actionId, "key", key));
    }

    @Override
    public void markSucceeded(String actionId, String requestId, String message, Instant completedAt) {
        jdbc.update("""
                UPDATE business_action
                SET status = 'SUCCEEDED', request_id = :requestId,
                    execution_message = :message, completed_at = :completedAt, failure_code = NULL
                WHERE action_id = :id
                """, Map.of("id", actionId, "requestId", requestId,
                "message", message, "completedAt", Timestamp.from(completedAt)));
    }

    @Override
    public void markCancelled(String actionId, String message, Instant completedAt) {
        jdbc.update("""
                UPDATE business_action
                SET status = 'CANCELLED', execution_message = :message,
                    completed_at = :completedAt, failure_code = NULL
                WHERE action_id = :id
                """, Map.of("id", actionId, "message", message,
                "completedAt", Timestamp.from(completedAt)));
    }

    @Override
    public void markFailed(String actionId, String code, Instant completedAt) {
        jdbc.update("UPDATE business_action SET status = 'FAILED', failure_code = :code, "
                        + "completed_at = :completedAt WHERE action_id = :id",
                Map.of("id", actionId, "code", code,
                        "completedAt", Timestamp.from(completedAt)));
    }

    @Override
    public void markExpired(String actionId, Instant completedAt) {
        jdbc.update("UPDATE business_action SET status = 'EXPIRED', failure_code = 'ACTION_EXPIRED', "
                        + "completed_at = :completedAt WHERE action_id = :id "
                        + "AND status = 'PENDING_CONFIRMATION'",
                Map.of("id", actionId, "completedAt", Timestamp.from(completedAt)));
    }

    @Override
    public int expirePending(Instant now) {
        return jdbc.update("UPDATE business_action SET status = 'EXPIRED', "
                        + "failure_code = 'ACTION_EXPIRED', completed_at = :now "
                        + "WHERE status = 'PENDING_CONFIRMATION' AND expires_at <= :now",
                Map.of("now", Timestamp.from(now)));
    }

    @Override
    public void maintainBounds(int maxCompleted) {
        jdbc.update("""
                DELETE FROM business_action
                WHERE action_id IN (
                    SELECT action_id FROM business_action
                    WHERE status IN ('CANCELLED', 'EXPIRED', 'FAILED')
                    ORDER BY completed_at DESC NULLS LAST, action_id DESC
                    OFFSET :maxCompleted)
                """, Map.of("maxCompleted", maxCompleted));
    }

    @Override
    public int size() {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM business_action", Map.of(), Integer.class);
        return count == null ? 0 : count;
    }

    private MapSqlParameterSource parameters(PendingAction action) {
        return new MapSqlParameterSource()
                .addValue("actionId", action.actionId())
                .addValue("actionType", action.actionType().name())
                .addValue("originTraceId", action.originTraceId())
                .addValue("employeeId", action.employeeId())
                .addValue("displayName", action.displayName())
                .addValue("startDate", action.startDate())
                .addValue("endDate", action.endDate())
                .addValue("halfDay", action.halfDay().name())
                .addValue("reason", action.reason())
                .addValue("days", action.days())
                .addValue("balanceBefore", action.balanceBefore())
                .addValue("balanceAfter", action.balanceAfter())
                .addValue("nonceDigest", action.confirmationNonceDigest())
                .addValue("status", action.status().name())
                .addValue("createdAt", Timestamp.from(action.createdAt()))
                .addValue("expiresAt", Timestamp.from(action.expiresAt()));
    }

    private PendingAction map(ResultSet rs, int rowNum) throws SQLException {
        return new PendingAction(
                rs.getString("action_id"), BusinessActionType.valueOf(rs.getString("action_type")),
                rs.getString("origin_trace_id"), rs.getString("employee_id"),
                rs.getString("display_name"), rs.getObject("start_date", java.time.LocalDate.class),
                rs.getObject("end_date", java.time.LocalDate.class),
                HalfDay.valueOf(rs.getString("half_day")), rs.getString("reason"),
                rs.getBigDecimal("days"), rs.getBigDecimal("balance_before"),
                rs.getBigDecimal("balance_after"), rs.getBytes("confirmation_nonce_digest"),
                ActionStatus.valueOf(rs.getString("status")),
                rs.getObject("idempotency_key", UUID.class), rs.getString("request_id"),
                rs.getString("execution_message"), rs.getString("failure_code"),
                instant(rs, "created_at"), instant(rs, "expires_at"), instant(rs, "completed_at"));
    }

    private Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }
}
