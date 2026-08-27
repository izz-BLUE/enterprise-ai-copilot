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
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Repository
public class JdbcPendingActionRepository implements PendingActionRepository {
    private static final String COLUMNS = """
            action_id, action_type, origin_trace_id, owner_user_id, conversation_id,
            employee_id, display_name, start_date, end_date, half_day, reason, days,
            balance_before, balance_after, confirmation_nonce_digest, status, idempotency_key,
            request_id, execution_message, failure_code, created_at, expires_at, completed_at,
            action_payload_json, agent_execution_id, hitl_wait_id
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
                    action_id, action_type, origin_trace_id, owner_user_id, conversation_id,
                    employee_id, display_name, start_date, end_date, half_day, reason, days,
                    balance_before, balance_after, confirmation_nonce_digest, status,
                    created_at, expires_at, action_payload_json, agent_execution_id, hitl_wait_id)
                VALUES (:actionId, :actionType, :originTraceId, :ownerUserId, :conversationId,
                    :employeeId, :displayName, :startDate, :endDate, :halfDay, :reason, :days,
                    :balanceBefore, :balanceAfter, :nonceDigest, :status, :createdAt, :expiresAt,
                    CAST(:actionPayloadJson AS jsonb), :agentExecutionId, :hitlWaitId)
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
    public Optional<PendingAction> findByHitlWaitId(String hitlWaitId) {
        return jdbc.query("SELECT " + COLUMNS
                        + " FROM business_action WHERE hitl_wait_id = :hitlWaitId",
                Map.of("hitlWaitId", hitlWaitId), rowMapper).stream().findFirst();
    }

    @Override
    public Optional<PendingAction> findByHitlWaitIdForUpdate(String hitlWaitId) {
        return jdbc.query("SELECT " + COLUMNS
                        + " FROM business_action WHERE hitl_wait_id = :hitlWaitId FOR UPDATE",
                Map.of("hitlWaitId", hitlWaitId), rowMapper).stream().findFirst();
    }

    @Override
    public void updateConfirmationNonceDigest(String actionId, byte[] nonceDigest) {
        jdbc.update("UPDATE business_action SET confirmation_nonce_digest = :nonceDigest "
                        + "WHERE action_id = :id", Map.of(
                "id", actionId, "nonceDigest", nonceDigest));
    }

    @Override
    public int countActive() {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM business_action "
                        + "WHERE status IN ('PENDING_CONFIRMATION', 'PROCESSING')",
                Map.of(), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public boolean hasActiveByOwnerAndConversation(String ownerUserId, String conversationId) {
        if (ownerUserId == null || conversationId == null) {
            return false;
        }
        Boolean exists = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM business_action "
                        + "WHERE owner_user_id = :owner AND conversation_id = :conversation "
                        + "AND status IN ('PENDING_CONFIRMATION', 'PROCESSING'))",
                Map.of("owner", ownerUserId, "conversation", conversationId), Boolean.class);
        return Boolean.TRUE.equals(exists);
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
    public List<PendingAction> findExpired(Instant now) {
        return jdbc.query("SELECT " + COLUMNS
                        + " FROM business_action WHERE status = 'PENDING_CONFIRMATION' "
                        + "AND expires_at <= :now",
                Map.of("now", Timestamp.from(now)), rowMapper);
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
                .addValue("ownerUserId", action.ownerUserId())
                .addValue("conversationId", action.conversationId())
                .addValue("employeeId", action.employeeId())
                .addValue("displayName", action.displayName())
                .addValue("startDate", action.startDate())
                .addValue("endDate", action.endDate())
                .addValue("halfDay", action.halfDay() == null ? null : action.halfDay().name())
                .addValue("reason", action.reason())
                .addValue("days", action.days())
                .addValue("balanceBefore", action.balanceBefore())
                .addValue("balanceAfter", action.balanceAfter())
                .addValue("nonceDigest", action.confirmationNonceDigest())
                .addValue("status", action.status().name())
                .addValue("createdAt", Timestamp.from(action.createdAt()))
                .addValue("expiresAt", Timestamp.from(action.expiresAt()))
                .addValue("actionPayloadJson", action.actionPayloadJson())
                .addValue("agentExecutionId", action.agentExecutionId())
                .addValue("hitlWaitId", action.hitlWaitId());
    }

    private PendingAction map(ResultSet rs, int rowNum) throws SQLException {
        return new PendingAction(
                rs.getString("action_id"), BusinessActionType.valueOf(rs.getString("action_type")),
                rs.getString("origin_trace_id"), rs.getString("owner_user_id"),
                rs.getString("conversation_id"), rs.getString("employee_id"),
                rs.getString("display_name"), rs.getObject("start_date", java.time.LocalDate.class),
                rs.getObject("end_date", java.time.LocalDate.class),
                rs.getString("half_day") == null ? null : HalfDay.valueOf(rs.getString("half_day")),
                rs.getString("reason"),
                rs.getBigDecimal("days"), rs.getBigDecimal("balance_before"),
                rs.getBigDecimal("balance_after"), rs.getBytes("confirmation_nonce_digest"),
                ActionStatus.valueOf(rs.getString("status")),
                rs.getObject("idempotency_key", UUID.class), rs.getString("request_id"),
                rs.getString("execution_message"), rs.getString("failure_code"),
                instant(rs, "created_at"), instant(rs, "expires_at"), instant(rs, "completed_at"),
                rs.getString("action_payload_json"), rs.getString("agent_execution_id"),
                rs.getString("hitl_wait_id"));
    }

    private Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }
}
