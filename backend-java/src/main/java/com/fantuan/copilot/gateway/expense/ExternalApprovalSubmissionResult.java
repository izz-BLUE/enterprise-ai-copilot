package com.fantuan.copilot.gateway.expense;

/** 用于提交重放和权威 status 读取的最小 Mock OA 响应。 */
public record ExternalApprovalSubmissionResult(String requestId, String status) {
    public boolean isSupportedStatus() {
        return "PENDING".equals(status) || "APPROVED".equals(status) || "REJECTED".equals(status);
    }

    public boolean isTerminal() {
        return "APPROVED".equals(status) || "REJECTED".equals(status);
    }
}
