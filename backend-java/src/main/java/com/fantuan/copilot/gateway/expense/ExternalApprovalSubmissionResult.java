package com.fantuan.copilot.gateway.expense;

/** Minimal Mock OA response used for submission replay and authoritative status reads. */
public record ExternalApprovalSubmissionResult(String requestId, String status) {
    public boolean isSupportedStatus() {
        return "PENDING".equals(status) || "APPROVED".equals(status) || "REJECTED".equals(status);
    }

    public boolean isTerminal() {
        return "APPROVED".equals(status) || "REJECTED".equals(status);
    }
}
