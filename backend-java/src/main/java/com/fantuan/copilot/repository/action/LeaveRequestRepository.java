package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.LeaveRequest;

import java.time.LocalDate;

public interface LeaveRequestRepository {
    boolean hasConflict(String employeeId, LocalDate startDate, LocalDate endDate);
    long nextNumber();
    void save(String sourceActionId, LeaveRequest request);
    int countBySourceActionId(String sourceActionId);
    int size();
}
