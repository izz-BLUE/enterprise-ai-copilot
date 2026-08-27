package com.fantuan.copilot.service.action;

/** Failure while refreshing an external approval status from its provider. */
public class ExpenseExternalApprovalStatusSyncException extends RuntimeException {
    public ExpenseExternalApprovalStatusSyncException(String message) {
        super(message);
    }

    public ExpenseExternalApprovalStatusSyncException(String message, Throwable cause) {
        super(message, cause);
    }
}
