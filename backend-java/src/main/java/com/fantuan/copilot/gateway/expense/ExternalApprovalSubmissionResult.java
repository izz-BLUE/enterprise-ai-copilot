package com.fantuan.copilot.gateway.expense;

/** Minimal provider response accepted by P3-5B1. */
public record ExternalApprovalSubmissionResult(String requestId, String status) {
}
