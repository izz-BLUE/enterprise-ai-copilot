package com.fantuan.copilot.repository.memory;

import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
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

@Repository
public class JdbcAiTaskMemoryRepository implements AiTaskMemoryRepository {

    private static final String COLUMNS = """
            user_id, conversation_id, task_type, status, task_state_json, summary,
            created_at, updated_at
            """;

    private final NamedParameterJdbcTemplate jdbc;
    private final RowMapper<AiTaskMemory> rowMapper = this::map;

    public JdbcAiTaskMemoryRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<AiTaskMemory> find(String userId, String conversationId) {
        return jdbc.query("SELECT " + COLUMNS + " FROM ai_task_memory "
                        + "WHERE user_id = :userId AND conversation_id = :conversationId",
                Map.of("userId", userId, "conversationId", conversationId),
                rowMapper).stream().findFirst();
    }

    @Override
    public void upsert(String userId, String conversationId, String taskType, TaskStatus status,
                       String taskStateJson, String summary) {
        MapSqlParameterSource p = new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("conversationId", conversationId)
                .addValue("taskType", taskType)
                .addValue("status", status.name())
                .addValue("taskStateJson", taskStateJson)
                .addValue("summary", summary);
        // ON CONFLICT (user_id, conversation_id) DO UPDATE —— PostgreSQL 专属 upsert 语法。
        // created_at 保留原值，updated_at 刷新为 CURRENT_TIMESTAMP。
        jdbc.update("""
                INSERT INTO ai_task_memory (
                    user_id, conversation_id, task_type, status, task_state_json, summary)
                VALUES (:userId, :conversationId, :taskType, :status, :taskStateJson, :summary)
                ON CONFLICT (user_id, conversation_id) DO UPDATE SET
                    task_type = EXCLUDED.task_type,
                    status = EXCLUDED.status,
                    task_state_json = EXCLUDED.task_state_json,
                    summary = EXCLUDED.summary,
                    updated_at = CURRENT_TIMESTAMP
                """, p);
    }

    @Override
    public int delete(String userId, String conversationId) {
        return jdbc.update("DELETE FROM ai_task_memory "
                        + "WHERE user_id = :userId AND conversation_id = :conversationId",
                Map.of("userId", userId, "conversationId", conversationId));
    }

    private AiTaskMemory map(ResultSet rs, int rowNum) throws SQLException {
        String rawStatus = rs.getString("status");
        if (TaskStatus.parse(rawStatus).isEmpty()) {
            throw new SQLException("ai_task_memory.status 含非法值: " + rawStatus);
        }
        TaskStatus status = TaskStatus.parse(rawStatus).get();
        return new AiTaskMemory(
                rs.getString("user_id"),
                rs.getString("conversation_id"),
                rs.getString("task_type"),
                status,
                rs.getString("task_state_json"),
                rs.getString("summary"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }
}