package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;

/** 当前 Enterprise OA 报销事实的传输边界。 */
public interface ExpenseAuthoritativeRevalidationGateway {
    ExpenseRevalidationResponse revalidate(ExpenseRevalidationRequest request, String traceId);
}
