package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ActionStatus;
import org.springframework.http.HttpStatus;

/** Infrastructure failure at confirm time; the pending action remains retryable. */
final class ExpenseRevalidationUnavailableException extends ActionException {
    ExpenseRevalidationUnavailableException(String actionId, ActionStatus status,
                                             Throwable cause) {
        super(HttpStatus.SERVICE_UNAVAILABLE, "EXPENSE_REVALIDATION_UNAVAILABLE",
                "报销当前状态暂时无法核验，请稍后重试。", actionId, status);
        initCause(cause);
    }
}
