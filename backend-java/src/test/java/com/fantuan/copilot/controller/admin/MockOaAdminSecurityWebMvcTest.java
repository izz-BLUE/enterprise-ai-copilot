package com.fantuan.copilot.controller.admin;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.config.SecurityConfig;
import com.fantuan.copilot.dto.admin.MockOaApprovalActionResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalListResponse;
import com.fantuan.copilot.dto.admin.MockOaApprovalView;
import com.fantuan.copilot.gateway.expense.MockOaAdminGateway;
import com.fantuan.copilot.security.SecurityErrorHandlers;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 验证管理员审批台只接受已验证 JWT 中的 ADMIN 角色。 */
@WebMvcTest(controllers = MockOaAdminController.class)
@Import({SecurityConfig.class, SecurityErrorHandlers.class, AdminLogBuffer.class})
@TestPropertySource(properties = {
        "auth.jwt.secret=test-secret-test-secret-test-secret-test",
        "auth.jwt.issuer=enterprise-ai-copilot",
        "auth.jwt.audience=enterprise-ai-copilot"
})
class MockOaAdminSecurityWebMvcTest {
    private static final String PATH = "/api/admin/mock-oa/expense-approvals";

    @Autowired MockMvc mockMvc;

    @MockitoBean MockOaAdminGateway gateway;
    @MockitoBean JwtEncoder jwtEncoder;
    @MockitoBean com.fantuan.copilot.auth.AppUserDetailsService appUserDetailsService;
    @MockitoBean JwtDecoder jwtDecoder;

    @Test
    void adminCanListApproveAndRejectWithoutAdminToken() throws Exception {
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("ADMIN", "U90001", "admin"));
        when(gateway.list(null)).thenReturn(new MockOaApprovalListResponse(List.of(view()), 1));
        when(gateway.decide("OA-EXP-1", "APPROVED"))
                .thenReturn(new MockOaApprovalActionResponse("OA-EXP-1", "APPROVED"));
        when(gateway.decide("OA-EXP-1", "REJECTED"))
                .thenReturn(new MockOaApprovalActionResponse("OA-EXP-1", "REJECTED"));

        mockMvc.perform(get(PATH).accept(MediaType.APPLICATION_JSON).with(bearer()))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.items[0].requestId").value("OA-EXP-1"));
        mockMvc.perform(post(PATH + "/OA-EXP-1/approve").with(bearer()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("APPROVED"));
        mockMvc.perform(post(PATH + "/OA-EXP-1/reject").with(bearer()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("REJECTED"));
    }

    @ParameterizedTest
    @CsvSource({
            "demo, U10000",
            "zhangsan, U10001",
            "lisi, U10002",
            "wangwu, U10003"
    })
    void nonAdminIdentitiesReceive403(String username, String userId) throws Exception {
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("EMPLOYEE", userId, username));

        mockMvc.perform(get(PATH).accept(MediaType.APPLICATION_JSON).with(bearer()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("FORBIDDEN"));
    }

    private MockOaApprovalView view() {
        return new MockOaApprovalView("OA-EXP-1", "PENDING", "EXP-1", "E10001", "TRIP-1",
                "COST-IT", new BigDecimal("100.00"), new BigDecimal("90.00"),
                Instant.parse("2026-08-31T00:00:00Z"));
    }

    private RequestPostProcessor bearer() {
        return request -> {
            request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer fake-token");
            return request;
        };
    }

    private Jwt buildJwt(String role, String userId, String username) {
        Map<String, Object> claims = new java.util.HashMap<>();
        claims.put("role", role);
        claims.put("username", username);
        claims.put("display_name", username);
        claims.put("employee_id", "E10001");
        return Jwt.withTokenValue("fake-token")
                .header("alg", "HS256")
                .issuedAt(Instant.now().minusSeconds(60))
                .expiresAt(Instant.now().plusSeconds(600))
                .issuer("enterprise-ai-copilot")
                .audience(List.of("enterprise-ai-copilot"))
                .subject(userId)
                .claims(c -> c.putAll(claims))
                .build();
    }
}
