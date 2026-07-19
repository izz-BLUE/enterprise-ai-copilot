package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoIdentityService;
import com.fantuan.copilot.service.demo.DemoRole;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.Instant;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class BusinessActionControllerTest {
    private BusinessActionService service;
    private DemoIdentityService identities;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        service = mock(BusinessActionService.class);
        identities = mock(DemoIdentityService.class);
        when(identities.requireIdentity("DEMO-001")).thenReturn(new DemoIdentity(
                "DEMO-001", "DEMO-001", "Demo User", DemoRole.EMPLOYEE));
        when(identities.requireIdentity(null)).thenThrow(new ActionException(
                HttpStatus.BAD_REQUEST, "DEMO_IDENTITY_REQUIRED", "请选择演示身份。", null, null));
        when(identities.requireIdentity("unknown")).thenThrow(new ActionException(
                HttpStatus.FORBIDDEN, "DEMO_IDENTITY_INVALID", "演示身份无效。", null, null));
        mockMvc = MockMvcBuilders.standaloneSetup(new BusinessActionController(service, identities))
                .setControllerAdvice(new BusinessActionExceptionHandler())
                .build();
    }

    @Test
    void missingAndUnknownIdentityUseSafeHttpSemantics() throws Exception {
        String body = "{\"confirmationNonce\":\"nonce\"}";
        mockMvc.perform(post("/api/agent/actions/act_test/cancel")
                        .requestAttr("traceId", "missing-identity")
                        .contentType("application/json").content(body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("DEMO_IDENTITY_REQUIRED"));

        mockMvc.perform(post("/api/agent/actions/act_test/cancel")
                        .header("X-Demo-User-Id", "unknown")
                        .requestAttr("traceId", "invalid-identity")
                        .contentType("application/json").content(body))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("DEMO_IDENTITY_INVALID"))
                .andExpect(content().string(org.hamcrest.Matchers.not(
                        org.hamcrest.Matchers.containsString("unknown"))));
    }

    @Test
    void confirmContractIncludesNoStoreAndOriginTraceId() throws Exception {
        ActionExecutionResponse response = new ActionExecutionResponse("act_test",
                BusinessActionType.ANNUAL_LEAVE_REQUEST, ActionStatus.SUCCEEDED,
                "LR-202607-0001", "模拟年假申请已提交。", false,
                Instant.parse("2026-07-16T01:00:00Z"), "origin", "confirm-trace");
        when(service.confirm(anyString(), anyString(), anyString(), anyString(), anyString(), any()))
                .thenReturn(response);

        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("X-Admin-Token", "admin")
                        .header("X-Demo-User-Id", "DEMO-001")
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
        when(service.cancel(anyString(), anyString(), anyString(), anyString(), any())).thenReturn(response);

        mockMvc.perform(post("/api/agent/actions/act_test/cancel")
                        .header("X-Admin-Token", "admin")
                        .header("X-Demo-User-Id", "DEMO-001")
                        .requestAttr("traceId", "cancel-trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.status").value("CANCELLED"));
    }

    @Test
    void actionErrorUsesDedicatedContractAndNoStore() throws Exception {
        when(service.confirm(anyString(), anyString(), anyString(),
                org.mockito.ArgumentMatchers.<String>any(), anyString(), any()))
                .thenThrow(new ActionException(HttpStatus.GONE, "ACTION_EXPIRED",
                        "该申请草稿已过期，请重新生成。", "act_test", ActionStatus.EXPIRED));

        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("Idempotency-Key", UUID.randomUUID())
                        .header("X-Demo-User-Id", "DEMO-001")
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

    @Test
    void infrastructureFailureReturnsSafeInternalError() throws Exception {
        when(service.confirm(anyString(), anyString(), anyString(),
                org.mockito.ArgumentMatchers.<String>any(), anyString(), any()))
                .thenThrow(new RuntimeException("database detail must not escape"));

        mockMvc.perform(post("/api/agent/actions/act_test/confirm")
                        .header("Idempotency-Key", UUID.randomUUID())
                        .header("X-Demo-User-Id", "DEMO-001")
                        .requestAttr("traceId", "safe-error-trace")
                        .contentType("application/json")
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isInternalServerError())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.errorCode").value("ACTION_INTERNAL_ERROR"))
                .andExpect(jsonPath("$.message").value("业务动作处理失败。"))
                .andExpect(content().string(org.hamcrest.Matchers.not(
                        org.hamcrest.Matchers.containsString("database detail"))));
    }
}
