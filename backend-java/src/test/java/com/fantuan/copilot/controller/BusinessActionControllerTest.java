package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Instant;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class BusinessActionControllerTest {
    private BusinessActionService service;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        service = mock(BusinessActionService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new BusinessActionController(service))
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
    }

    @Test
    void confirmContractIncludesNoStoreAndOriginTraceId() throws Exception {
        ActionExecutionResponse response = new ActionExecutionResponse("act_test",
                BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.SUCCEEDED,
                "LR-202607-0001", "模拟年假申请已提交。", false,
                Instant.parse("2026-07-16T01:00:00Z"), "origin", "confirm-trace");
        when(service.confirm(anyString(), anyString(), anyString(), anyString(), anyString()))
                .thenReturn(response);

        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("X-Admin-Token", "admin")
                        .header("Idempotency-Key", UUID.randomUUID())
                        .requestAttr("traceId", "confirm-trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.status").value("SUCCEEDED"))
                .andExpect(jsonPath("$.requestId").value("LR-202607-0001"))
                .andExpect(jsonPath("$.originTraceId").value("origin"))
                .andExpect(jsonPath("$.traceId").value("confirm-trace"));
    }

    @Test
    void cancelContractIncludesNoStore() throws Exception {
        ActionExecutionResponse response = new ActionExecutionResponse("act_test",
                BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.CANCELLED,
                null, "申请草稿已取消。", false, Instant.parse("2026-07-16T01:00:00Z"),
                "origin", "cancel-trace");
        when(service.cancel(anyString(), anyString(), anyString(), anyString())).thenReturn(response);

        mockMvc.perform(post("/api/agent/actions/act_test/cancel")
                        .header("X-Admin-Token", "admin")
                        .requestAttr("traceId", "cancel-trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.status").value("CANCELLED"));
    }

    @Test
    void actionErrorUsesDedicatedContractAndNoStore() throws Exception {
        when(service.confirm(anyString(), anyString(), anyString(), org.mockito.ArgumentMatchers.<String>any(), anyString()))
                .thenThrow(new ActionException(HttpStatus.GONE, "ACTION_EXPIRED",
                        "该申请草稿已过期，请重新生成。", "act_test", ActionStatus.EXPIRED));

        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("Idempotency-Key", UUID.randomUUID())
                        .requestAttr("traceId", "error-trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isGone())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.errorCode").value("ACTION_EXPIRED"))
                .andExpect(jsonPath("$.traceId").value("error-trace"));
    }

    @Test
    void requestBodyCannotCarryBusinessFields() throws Exception {
        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("Idempotency-Key", UUID.randomUUID())
                        .requestAttr("traceId", "trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\",\"reason\":\"tamper\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("INVALID_REQUEST"));
    }
}
