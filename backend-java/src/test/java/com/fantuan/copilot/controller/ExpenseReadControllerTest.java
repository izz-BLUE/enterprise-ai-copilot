package com.fantuan.copilot.controller;

import com.fantuan.copilot.model.action.ExpenseClaim;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.service.action.ExpenseReadService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class ExpenseReadControllerTest {

    private ExpenseClaimRepository claims;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        claims = mock(ExpenseClaimRepository.class);
        ExpenseReadService service = new ExpenseReadService(claims);
        ExpenseReadController controller = new ExpenseReadController(service);
        ReflectionTestUtils.setField(controller, "expectedInternalToken", "internal-secret");
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
    }

    private ExpenseClaim claim(String expenseId, String employeeId) {
        return new ExpenseClaim(expenseId, "act-src", employeeId, "TRIP-001",
                "COST-DEFAULT", new BigDecimal("1830"), new BigDecimal("1730"),
                ExpenseStatus.SUBMITTED, Instant.parse("2026-08-26T10:00:00Z"),
                Instant.parse("2026-08-26T10:00:00Z"));
    }

    @Test
    void statusMissingInternalTokenIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-20260826-000001")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-no-token"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("EXPENSE_READ_FORBIDDEN"));
        verify(claims, never()).findByExpenseId(anyString());
    }

    @Test
    void statusMissingEmployeeIdIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-20260826-000001")
                        .header("X-Internal-Token", "internal-secret")
                        .requestAttr("traceId", "trace-no-emp"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("EMPLOYEE_ID_REQUIRED"));
        verify(claims, never()).findByExpenseId(anyString());
    }

    @Test
    void statusValidRequestReturnsJavaAuthority() throws Exception {
        when(claims.findByExpenseId("EXP-20260826-000001"))
                .thenReturn(Optional.of(claim("EXP-20260826-000001", "DEMO-001")));
        mockMvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-20260826-000001")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-ok"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.expenseId").value("EXP-20260826-000001"))
                .andExpect(jsonPath("$.status").value("SUBMITTED"))
                .andExpect(jsonPath("$.claimedAmount").value(1830.0));
        verify(claims).findByExpenseId("EXP-20260826-000001");
    }

    @Test
    void statusCrossEmployeeReadIs404() throws Exception {
        /* V2 §二十四：expense.employeeId != 可信 employeeId → 不能跨员工读取。 */
        when(claims.findByExpenseId("EXP-20260826-000001"))
                .thenReturn(Optional.of(claim("EXP-20260826-000001", "DEMO-002")));
        mockMvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-20260826-000001")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-cross"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.errorCode").value("EXPENSE_NOT_FOUND"));
    }

    @Test
    void statusExpenseMissingIs404() throws Exception {
        when(claims.findByExpenseId(anyString())).thenReturn(Optional.empty());
        mockMvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-NOT-EXIST")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-missing"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.errorCode").value("EXPENSE_NOT_FOUND"));
    }

    @Test
    void recentValidRequestReturnsList() throws Exception {
        when(claims.findRecentByEmployee("DEMO-001", 10))
                .thenReturn(List.of(claim("EXP-20260826-000001", "DEMO-001")));
        mockMvc.perform(get("/api/internal/expense/recent")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-recent"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.employeeId").value("DEMO-001"))
                .andExpect(jsonPath("$.total").value(1));
    }

    @Test
    void disabledInternalTokenReturns503() throws Exception {
        ExpenseReadController controller = new ExpenseReadController(
                new ExpenseReadService(mock(ExpenseClaimRepository.class)));
        ReflectionTestUtils.setField(controller, "expectedInternalToken", "");
        MockMvc mvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
        mvc.perform(get("/api/internal/expense/status")
                        .param("expenseId", "EXP-20260826-000001")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "DEMO-001")
                        .requestAttr("traceId", "trace-disabled"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.errorCode").value("EXPENSE_READ_DISABLED"));
    }
}
