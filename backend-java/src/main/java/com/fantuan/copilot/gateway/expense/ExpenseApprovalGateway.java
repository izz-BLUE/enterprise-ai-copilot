package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.model.action.ExpenseClaim;

/** 将本地已提交报销提交给审批 provider 的 Java 边界。 */
public interface ExpenseApprovalGateway {
    ExternalApprovalSubmissionResult submit(ExpenseClaim claim);

    ExternalApprovalSubmissionResult getStatus(String externalRequestId);
}
