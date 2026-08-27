package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;

/** Transport boundary for current Enterprise OA expense facts. */
public interface ExpenseAuthoritativeRevalidationGateway {
    ExpenseRevalidationResponse revalidate(ExpenseRevalidationRequest request, String traceId);
}
