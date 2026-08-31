package com.fantuan.copilot.dto.admin;

import java.math.BigDecimal;
import java.time.Instant;

/** 模拟 OA 管理列表中的业务展示字段，不包含内部凭据或幂等字段。 */
public record MockOaApprovalView(
        String requestId,
        String status,
        String expenseId,
        String employeeId,
        String tripId,
        String costCenter,
        BigDecimal claimedAmount,
        BigDecimal reimbursableAmount,
        Instant createdAt) {
}
