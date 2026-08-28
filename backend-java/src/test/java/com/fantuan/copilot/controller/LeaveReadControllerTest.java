package com.fantuan.copilot.controller;

import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.LeaveRequestRepository;
import com.fantuan.copilot.service.action.LeaveReadService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.math.BigDecimal;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class LeaveReadControllerTest {

    private LeaveAccountRepository accounts;
    private LeaveRequestRepository requests;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        accounts = mock(LeaveAccountRepository.class);
        requests = mock(LeaveRequestRepository.class);
        LeaveReadService service = new LeaveReadService(accounts, requests);
        LeaveReadController controller = new LeaveReadController(service);
        // 注入 @Value 字段,模拟真实启动配置
        ReflectionTestUtils.setField(controller, "expectedInternalToken", "internal-secret");
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
    }

    @Test
    void balanceMissingInternalTokenIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-no-token"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_FORBIDDEN"));
        verify(accounts, never()).findBalance(anyString());
    }

    @Test
    void balanceWrongInternalTokenIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "wrong-token")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-wrong-token"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_FORBIDDEN"));
        verify(accounts, never()).findBalance(anyString());
    }

    @Test
    void balanceMissingEmployeeIdIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-secret")
                        .requestAttr("traceId", "trace-no-emp"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("EMPLOYEE_ID_REQUIRED"));
        verify(accounts, never()).findBalance(anyString());
    }

    @Test
    void balanceValidRequestReturnsAccountAndEmployeeId() throws Exception {
        when(accounts.findBalance("E10001")).thenReturn(Optional.of(new BigDecimal("3.5")));
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-ok"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.employeeId").value("E10001"))
                .andExpect(jsonPath("$.annualBalance").value(3.5));
        verify(accounts).findBalance("E10001");
    }

    @Test
    void balanceAccountMissingIs404() throws Exception {
        when(accounts.findBalance(anyString())).thenReturn(Optional.empty());
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-noacc"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_ACCOUNT_NOT_FOUND"));
    }

    @Test
    void requestsMissingInternalTokenIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/leave/requests")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-list-no-token"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_FORBIDDEN"));
        verify(requests, never()).findRecentByEmployee(anyString(), anyInt());
    }

    @Test
    void requestsMissingEmployeeIdIsRejected() throws Exception {
        mockMvc.perform(get("/api/internal/leave/requests")
                        .header("X-Internal-Token", "internal-secret")
                        .requestAttr("traceId", "trace-list-no-emp"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("EMPLOYEE_ID_REQUIRED"));
        verify(requests, never()).findRecentByEmployee(anyString(), anyInt());
    }

    @Test
    void requestsValidRequestForwardsEmployeeIdAndLimit() throws Exception {
        when(requests.findRecentByEmployee(eq("E10001"), eq(5))).thenReturn(java.util.List.of());
        mockMvc.perform(get("/api/internal/leave/requests")
                        .param("limit", "5")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-list-ok"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.employeeId").value("E10001"))
                .andExpect(jsonPath("$.total").value(0));
        verify(requests).findRecentByEmployee("E10001", 5);
    }

    @Test
    void disabledInternalTokenReturns503() throws Exception {
        // 模拟 production leave.read.internal-token 未配置
        accounts = mock(LeaveAccountRepository.class);
        requests = mock(LeaveRequestRepository.class);
        LeaveReadService service = new LeaveReadService(accounts, requests);
        LeaveReadController controller = new LeaveReadController(service);
        ReflectionTestUtils.setField(controller, "expectedInternalToken", "");
        MockMvc mvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
        mvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-secret")
                        .header("X-Employee-Id", "E10001")
                        .requestAttr("traceId", "trace-disabled"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_DISABLED"));
    }
}
