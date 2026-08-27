package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseClaim;

/** Java boundary for submitting a locally committed expense to an approval provider. */
public interface ExpenseApprovalGateway {
    ExternalApprovalSubmissionResult submit(ExpenseClaim claim);
}
