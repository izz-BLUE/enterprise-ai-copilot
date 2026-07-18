package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.LeaveRequest;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.sql.Timestamp;
import java.util.Map;

@Repository
public class JdbcLeaveRequestRepository implements LeaveRequestRepository {
    private final NamedParameterJdbcTemplate jdbc;

    public JdbcLeaveRequestRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public boolean hasConflict(String employeeId, LocalDate startDate, LocalDate endDate) {
        Boolean exists = jdbc.queryForObject("""
                SELECT EXISTS (
                    SELECT 1 FROM leave_request
                    WHERE employee_id = :employeeId
                      AND start_date <= :endDate
                      AND end_date >= :startDate)
                """, Map.of("employeeId", employeeId, "startDate", startDate,
                "endDate", endDate), Boolean.class);
        return Boolean.TRUE.equals(exists);
    }

    @Override
    public long nextNumber() {
        Long value = jdbc.queryForObject("SELECT nextval('leave_request_number_seq')",
                Map.of(), Long.class);
        if (value == null) {
            throw new IllegalStateException("Leave request sequence unavailable");
        }
        return value;
    }

    @Override
    public void save(String sourceActionId, LeaveRequest request) {
        jdbc.update("""
                INSERT INTO leave_request(request_id, source_action_id, employee_id, leave_type,
                    start_date, end_date, half_day, days, submitted_at)
                VALUES (:requestId, :sourceActionId, :employeeId, :leaveType,
                    :startDate, :endDate, :halfDay, :days, :submittedAt)
                """, Map.of("requestId", request.requestId(), "sourceActionId", sourceActionId,
                "employeeId", request.employeeId(), "leaveType", request.leaveType(),
                "startDate", request.startDate(), "endDate", request.endDate(),
                "halfDay", request.halfDay().name(), "days", request.days(),
                "submittedAt", Timestamp.from(request.createdAt())));
    }

    @Override
    public int countBySourceActionId(String sourceActionId) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM leave_request "
                + "WHERE source_action_id = :id", Map.of("id", sourceActionId), Integer.class);
        return count == null ? 0 : count;
    }

    @Override
    public int size() {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM leave_request", Map.of(), Integer.class);
        return count == null ? 0 : count;
    }
}
