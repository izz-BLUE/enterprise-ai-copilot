package com.fantuan.copilot.repository.action;

import com.fantuan.copilot.model.action.LeaveRequest;

import java.time.LocalDate;
import java.util.List;

public interface LeaveRequestRepository {
    boolean hasConflict(String employeeId, LocalDate startDate, LocalDate endDate);
    long nextNumber();
    void save(String sourceActionId, LeaveRequest request);
    int countBySourceActionId(String sourceActionId);
    int size();

    /**
     * 按 employee_id 倒序拉取最近的请假记录，最多 limit 条；只读企业 Tool 使用。
     * 不提供按任意员工查询的能力，调用方必须传入已经过身份校验的 employeeId。
     */
    List<LeaveRequest> findRecentByEmployee(String employeeId, int limit);
}
