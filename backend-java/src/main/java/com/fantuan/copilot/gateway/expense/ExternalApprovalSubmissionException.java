package com.fantuan.copilot.gateway.expense;

/** 本地权威动作提交后发生的外部传输/结果失败。 */
public class ExternalApprovalSubmissionException extends RuntimeException {
    public ExternalApprovalSubmissionException(String message) { super(message); }
    public ExternalApprovalSubmissionException(String message, Throwable cause) { super(message, cause); }
}
