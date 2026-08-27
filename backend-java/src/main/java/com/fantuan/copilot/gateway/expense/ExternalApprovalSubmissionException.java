package com.fantuan.copilot.gateway.expense;

/** External transport/result failure after the authoritative local action has committed. */
public class ExternalApprovalSubmissionException extends RuntimeException {
    public ExternalApprovalSubmissionException(String message) { super(message); }
    public ExternalApprovalSubmissionException(String message, Throwable cause) { super(message, cause); }
}
