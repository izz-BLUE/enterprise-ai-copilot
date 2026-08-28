package com.fantuan.copilot.repository.task;

import com.fantuan.copilot.model.task.TaskExecution;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
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

@Repository
public class JdbcTaskExecutionRepository implements TaskExecutionRepository {
    private static final String COLUMNS = """
            task_group_id, task_id, owner_user_id, conversation_id, sequence_no,
            task_type, task_text, clarification_context, status, action_id,
            created_at, updated_at, completed_at
            """;

    private final NamedParameterJdbcTemplate jdbc;

    public JdbcTaskExecutionRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void saveAll(List<TaskExecution> executions) {
        for (TaskExecution execution : executions) {
            jdbc.update("""
                    INSERT INTO task_execution(
                        task_group_id, task_id, owner_user_id, conversation_id,
                        sequence_no, task_type, task_text, clarification_context,
                        status, action_id, created_at, updated_at, completed_at)
                    VALUES (:taskGroupId, :taskId, :ownerUserId, :conversationId,
                        :sequenceNo, :taskType, :taskText, :clarificationContext,
                        :status, :actionId, :createdAt, :updatedAt, :completedAt)
                    """, parameters(execution));
        }
    }

    @Override
    public Optional<TaskExecution> findByTaskId(String taskId) {
        return find("task_id = :taskId", Map.of("taskId", taskId), false);
    }

    @Override
    public Optional<TaskExecution> findByTaskIdForUpdate(String taskId) {
        return find("task_id = :taskId", Map.of("taskId", taskId), true);
    }

    @Override
    public Optional<TaskExecution> findByActionId(String actionId) {
        return find("action_id = :actionId", Map.of("actionId", actionId), false);
    }

    @Override
    public Optional<TaskExecution> findByActionIdForUpdate(String actionId) {
        return find("action_id = :actionId", Map.of("actionId", actionId), true);
    }

    @Override
    public Optional<TaskExecution> findInteractiveByOwnerAndConversationForUpdate(
            String ownerUserId, String conversationId) {
        return jdbc.query("SELECT " + COLUMNS + """
                FROM task_execution
                WHERE owner_user_id = :ownerUserId
                  AND conversation_id = :conversationId
                  AND status IN ('WAITING_USER', 'WAITING_CLARIFICATION', 'RUNNING')
                ORDER BY CASE status
                    WHEN 'WAITING_USER' THEN 1
                    WHEN 'WAITING_CLARIFICATION' THEN 2
                    WHEN 'RUNNING' THEN 3
                    ELSE 4 END,
                    sequence_no
                LIMIT 1 FOR UPDATE
                """, Map.of("ownerUserId", ownerUserId, "conversationId", conversationId),
                this::map).stream().findFirst();
    }

    @Override
    public List<TaskExecution> findByOwnerAndConversationForUpdate(
            String ownerUserId, String conversationId) {
        return jdbc.query("SELECT " + COLUMNS + """
                FROM task_execution
                WHERE owner_user_id = :ownerUserId
                  AND conversation_id = :conversationId
                ORDER BY created_at, task_group_id, sequence_no
                FOR UPDATE
                """, Map.of("ownerUserId", ownerUserId, "conversationId", conversationId),
                this::map);
    }

    @Override
    public Optional<TaskExecution> findPendingByGroupAndSequenceForUpdate(
            String taskGroupId, int sequenceNo) {
        return find("task_group_id = :taskGroupId AND sequence_no = :sequenceNo "
                        + "AND status = 'PENDING'",
                Map.of("taskGroupId", taskGroupId, "sequenceNo", sequenceNo), true);
    }

    @Override
    public List<TaskExecution> findByGroup(String taskGroupId) {
        return jdbc.query("SELECT " + COLUMNS + " FROM task_execution "
                        + "WHERE task_group_id = :taskGroupId ORDER BY sequence_no",
                Map.of("taskGroupId", taskGroupId), this::map);
    }

    @Override
    public boolean updateStatus(String taskId, TaskExecutionStatus expected,
                                TaskExecutionStatus target, Instant updatedAt,
                                Instant completedAt) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("taskId", taskId)
                .addValue("expected", expected.name())
                .addValue("target", target.name())
                .addValue("updatedAt", Timestamp.from(updatedAt))
                .addValue("completedAt", completedAt == null ? null : Timestamp.from(completedAt));
        int changed = jdbc.update("""
                UPDATE task_execution
                SET status = :target, updated_at = :updatedAt, completed_at = :completedAt
                WHERE task_id = :taskId AND status = :expected
                """, parameters);
        return changed == 1;
    }

    @Override
    public boolean updateStatusByActionId(String actionId, TaskExecutionStatus target,
                                          Instant updatedAt, Instant completedAt) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("actionId", actionId)
                .addValue("target", target.name())
                .addValue("updatedAt", Timestamp.from(updatedAt))
                .addValue("completedAt", completedAt == null ? null : Timestamp.from(completedAt));
        int changed = jdbc.update("""
                UPDATE task_execution
                SET status = :target, updated_at = :updatedAt, completed_at = :completedAt
                WHERE action_id = :actionId
                  AND status IN ('WAITING_EXTERNAL', 'RUNNING', 'WAITING_USER')
                """, parameters);
        return changed == 1;
    }

    @Override
    public boolean markWaitingUser(String taskId, String actionId, Instant updatedAt) {
        int changed = jdbc.update("""
                UPDATE task_execution
                SET status = 'WAITING_USER', action_id = :actionId, updated_at = :updatedAt
                WHERE task_id = :taskId
                  AND status IN ('RUNNING', 'WAITING_USER')
                  AND (action_id IS NULL OR action_id = :actionId)
                """, Map.of("taskId", taskId, "actionId", actionId,
                "updatedAt", Timestamp.from(updatedAt)));
        return changed == 1;
    }

    @Override
    public boolean updateClarificationContext(String taskId, String context, Instant updatedAt) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("taskId", taskId)
                .addValue("context", context)
                .addValue("updatedAt", Timestamp.from(updatedAt));
        int changed = jdbc.update("""
                UPDATE task_execution
                SET clarification_context = :context, updated_at = :updatedAt,
                    status = 'RUNNING'
                WHERE task_id = :taskId AND status = 'WAITING_CLARIFICATION'
                """, parameters);
        return changed == 1;
    }

    private Optional<TaskExecution> find(String predicate, Map<String, ?> parameters,
                                         boolean forUpdate) {
        String suffix = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query("SELECT " + COLUMNS + " FROM task_execution WHERE "
                        + predicate + suffix, parameters, this::map).stream().findFirst();
    }

    private MapSqlParameterSource parameters(TaskExecution execution) {
        return new MapSqlParameterSource()
                .addValue("taskGroupId", execution.taskGroupId())
                .addValue("taskId", execution.taskId())
                .addValue("ownerUserId", execution.ownerUserId())
                .addValue("conversationId", execution.conversationId())
                .addValue("sequenceNo", execution.sequenceNo())
                .addValue("taskType", execution.taskType().name())
                .addValue("taskText", execution.taskText())
                .addValue("clarificationContext", execution.clarificationContext())
                .addValue("status", execution.status().name())
                .addValue("actionId", execution.actionId())
                .addValue("createdAt", Timestamp.from(execution.createdAt()))
                .addValue("updatedAt", Timestamp.from(execution.updatedAt()))
                .addValue("completedAt", execution.completedAt() == null
                        ? null : Timestamp.from(execution.completedAt()));
    }

    private TaskExecution map(ResultSet rs, int rowNum) throws SQLException {
        return new TaskExecution(
                rs.getString("task_group_id"),
                rs.getString("task_id"),
                rs.getString("owner_user_id"),
                rs.getString("conversation_id"),
                rs.getInt("sequence_no"),
                TaskType.valueOf(rs.getString("task_type")),
                rs.getString("task_text"),
                rs.getString("clarification_context"),
                TaskExecutionStatus.valueOf(rs.getString("status")),
                rs.getString("action_id"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"),
                instant(rs, "completed_at"));
    }

    private Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }
}
