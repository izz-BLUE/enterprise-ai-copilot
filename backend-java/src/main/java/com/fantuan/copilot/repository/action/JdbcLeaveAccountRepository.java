package com.fantuan.copilot.repository.action;

import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.Instant;
import java.sql.Timestamp;
import java.util.Map;
import java.util.Optional;

@Repository
public class JdbcLeaveAccountRepository implements LeaveAccountRepository {
    private final NamedParameterJdbcTemplate jdbc;

    public JdbcLeaveAccountRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void initialize(String employeeId, String displayName, BigDecimal balance, Instant now) {
        jdbc.update("""
                INSERT INTO leave_account(employee_id, display_name, annual_balance, created_at, updated_at)
                VALUES (:employeeId, :displayName, :balance, :now, :now)
                ON CONFLICT (employee_id) DO NOTHING
                """, Map.of("employeeId", employeeId, "displayName", displayName,
                "balance", balance, "now", Timestamp.from(now)));
    }

    @Override
    public Optional<BigDecimal> findBalanceForUpdate(String employeeId) {
        return jdbc.query("SELECT annual_balance FROM leave_account "
                        + "WHERE employee_id = :id FOR UPDATE", Map.of("id", employeeId),
                (rs, rowNum) -> rs.getBigDecimal("annual_balance")).stream().findFirst();
    }

    @Override
    public Optional<BigDecimal> findBalance(String employeeId) {
        return jdbc.query("SELECT annual_balance FROM leave_account WHERE employee_id = :id",
                Map.of("id", employeeId), (rs, rowNum) -> rs.getBigDecimal("annual_balance"))
                .stream().findFirst();
    }

    @Override
    public void updateBalance(String employeeId, BigDecimal balance, Instant now) {
        int updated = jdbc.update("UPDATE leave_account SET annual_balance = :balance, "
                        + "updated_at = :now WHERE employee_id = :id",
                Map.of("id", employeeId, "balance", balance, "now", Timestamp.from(now)));
        if (updated != 1) {
            throw new IllegalStateException("Leave account update failed");
        }
    }
}
