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
import java.util.List;
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

    /**
     * 状态机受限写入，单条 SQL 原子完成（PostgreSQL）：
     *   - SELECT 分支条件：status=ACTIVE（允许首条创建）或记录已存在（触发
     *     ON CONFLICT 走条件更新）；无记录 + 写终态 → SELECT 0 行，不写入；
     *   - ON CONFLICT DO UPDATE 仅在现有 status 属于合法来源集合时覆盖
     *     （WHERE 子句控制）；
     *   - 条件不满足 → 整条语句 0 行影响 → 返回 false，不写入任何内容。
     * 并发下由 PostgreSQL 行级锁序列化，不会出现"终态被后写覆盖"的竞态。
     */
    @Override
    public boolean upsert(String userId, String conversationId, String taskType, TaskStatus status,
                          String taskStateJson, String summary) {
        MapSqlParameterSource p = new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("conversationId", conversationId)
                .addValue("taskType", taskType)
                .addValue("status", status.name())
                .addValue("taskStateJson", taskStateJson)
                .addValue("summary", summary)
                .addValue("allowedFrom", allowedFrom(status));
        int affected = jdbc.update("""
                INSERT INTO ai_task_memory (
                    user_id, conversation_id, task_type, status, task_state_json, summary)
                SELECT :userId, :conversationId, :taskType, :status, :taskStateJson, :summary
                WHERE :status = 'ACTIVE'
                   OR EXISTS (
                       SELECT 1 FROM ai_task_memory
                       WHERE user_id = :userId AND conversation_id = :conversationId)
                ON CONFLICT (user_id, conversation_id) DO UPDATE SET
                    task_type = EXCLUDED.task_type,
                    status = EXCLUDED.status,
                    task_state_json = EXCLUDED.task_state_json,
                    summary = EXCLUDED.summary,
                    updated_at = CURRENT_TIMESTAMP
                WHERE ai_task_memory.status IN (:allowedFrom)
                """, p);
        return affected > 0;
    }

    /**
     * 终态收口：ACTIVE → 终态（及同终态幂等重放），保留原内容只改 status。
     * 记录不存在 / 已是另一终态 → 0 行 → false（调用方无副作用跳过）。
     */
    @Override
    public boolean transitionToTerminal(String userId, String conversationId, TaskStatus target) {
        int affected = jdbc.update("""
                UPDATE ai_task_memory
                SET status = :target, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = :userId AND conversation_id = :conversationId
                  AND status IN (:allowedFrom)
                """, new MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("conversationId", conversationId)
                .addValue("target", target.name())
                .addValue("allowedFrom", allowedFrom(target)));
        return affected > 0;
    }

    /** 状态机合法来源集合：目标状态可从哪些现有状态转换而来。 */
    private static List<String> allowedFrom(TaskStatus target) {
        return switch (target) {
            case ACTIVE -> List.of("ACTIVE");
            case COMPLETED -> List.of("ACTIVE", "COMPLETED");
            case ABANDONED -> List.of("ACTIVE", "ABANDONED");
        };
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