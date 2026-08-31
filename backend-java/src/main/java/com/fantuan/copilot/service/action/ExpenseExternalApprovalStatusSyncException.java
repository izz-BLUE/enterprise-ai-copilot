package com.fantuan.copilot.service.action;

/** 从 provider 刷新外部审批 status 时发生的失败。 */
public class ExpenseExternalApprovalStatusSyncException extends RuntimeException {
    public ExpenseExternalApprovalStatusSyncException(String message) {
        super(message);
    }

    public ExpenseExternalApprovalStatusSyncException(String message, Throwable cause) {
        super(message, cause);
    }
}
