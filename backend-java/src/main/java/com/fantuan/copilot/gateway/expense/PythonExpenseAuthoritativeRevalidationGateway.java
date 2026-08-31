package com.fantuan.copilot.gateway.expense;

import com.fantuan.copilot.dto.action.ExpenseRevalidationRequest;
import com.fantuan.copilot.dto.action.ExpenseRevalidationResponse;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;

/** Java 到窄范围 Python adapter 的边界；不涉及 Planner 或 LangGraph。 */
@Component
public class PythonExpenseAuthoritativeRevalidationGateway
        implements ExpenseAuthoritativeRevalidationGateway {
    private static final String PATH = "/agent/internal/expense/revalidate";

    private final PythonAgentGateway pythonAgentGateway;

    public PythonExpenseAuthoritativeRevalidationGateway(PythonAgentGateway pythonAgentGateway) {
        this.pythonAgentGateway = pythonAgentGateway;
    }

    @Override
    public ExpenseRevalidationResponse revalidate(ExpenseRevalidationRequest request,
                                                  String traceId) {
        return pythonAgentGateway.post(PATH, request, new HttpHeaders(),
                ExpenseRevalidationResponse.class, traceId);
    }
}
