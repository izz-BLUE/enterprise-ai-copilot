package com.fantuan.copilot.auth;

import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.util.Map;
import java.util.Optional;

@Repository
public class JdbcAppUserRepository implements AppUserRepository {
    private final NamedParameterJdbcTemplate jdbc;

    public JdbcAppUserRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<AppUser> findByUsername(String username) {
        return find("SELECT user_id, username, password_hash, employee_id, display_name, role, "
                + "enabled, created_at FROM app_user WHERE username = :value", username);
    }

    @Override
    public Optional<AppUser> findByUserId(String userId) {
        return find("SELECT user_id, username, password_hash, employee_id, display_name, role, "
                + "enabled, created_at FROM app_user WHERE user_id = :value", userId);
    }

    @Override
    public void insert(AppUser user) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("userId", user.userId())
                .addValue("username", user.username())
                .addValue("passwordHash", user.passwordHash())
                .addValue("employeeId", user.employeeId())
                .addValue("displayName", user.displayName())
                .addValue("role", user.role().name())
                .addValue("enabled", user.enabled())
                .addValue("createdAt", Timestamp.from(user.createdAt()));
        jdbc.update("""
                INSERT INTO app_user(user_id, username, password_hash, employee_id,
                                     display_name, role, enabled, created_at)
                VALUES (:userId, :username, :passwordHash, :employeeId,
                        :displayName, :role, :enabled, :createdAt)
                """, parameters);
    }

    private Optional<AppUser> find(String sql, String value) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(sql, Map.of("value", value),
                    (rs, rowNum) -> new AppUser(
                            rs.getString("user_id"),
                            rs.getString("username"),
                            rs.getString("password_hash"),
                            rs.getString("employee_id"),
                            rs.getString("display_name"),
                            AuthRole.valueOf(rs.getString("role")),
                            rs.getBoolean("enabled"),
                            rs.getTimestamp("created_at").toInstant())));
        } catch (EmptyResultDataAccessException exception) {
            return Optional.empty();
        }
    }
}
