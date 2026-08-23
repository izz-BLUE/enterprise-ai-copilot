package com.fantuan.copilot.controller.admin;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import com.fantuan.copilot.config.SecurityConfig;
import com.fantuan.copilot.security.SecurityErrorHandlers;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 真实 Spring Security 过滤链验证 /api/admin/** 的 hasRole("ADMIN") 授权。
 *
 * 设计目标：
 *   - 使用 @WebMvcTest + @Import(SecurityConfig) 装载真实 SecurityConfig，
 *     让 Spring Security 过滤链（authorizeHttpRequests + oauth2ResourceServer(jwt)）
 *     真实生效；
 *   - 通过 mock JwtDecoder 把 Authorization: Bearer <token> 解析成 Jwt，
 *     SecurityConfig 内部的 JwtPrincipalConverter 会把 claim role 翻译为
 *     ROLE_<role>，由 hasRole("ADMIN") 拦截；
 *   - 不依赖 Docker / Testcontainers / MySQL / spring-security-test。
 */
@WebMvcTest(controllers = AdminLogController.class)
@Import({SecurityConfig.class, SecurityErrorHandlers.class, AdminLogBuffer.class})
@TestPropertySource(properties = {
        "auth.jwt.secret=test-secret-test-secret-test-secret-test",
        "auth.jwt.issuer=enterprise-ai-copilot",
        "auth.jwt.audience=enterprise-ai-copilot"
})
class AdminLogSecurityWebMvcTest {

    @Autowired MockMvc mockMvc;
    @Autowired AdminLogBuffer adminLogBuffer;

    // SecurityConfig 装配的真实依赖；mock 它们以避免完整 bean 启动。
    @MockBean JwtEncoder jwtEncoder;
    @MockBean com.fantuan.copilot.auth.AppUserDetailsService appUserDetailsService;
    @MockBean com.fantuan.copilot.service.demo.DemoIdentityService demoIdentityService;

    // 用 mock JwtDecoder 把任意 Bearer token 翻译成带 role claim 的 Jwt。
    // JwtPrincipalConverter 会把 role claim → ROLE_<role>，从而被 hasRole("ADMIN") 接受。
    @MockBean JwtDecoder jwtDecoder;

    private RequestPostProcessor bearer(String role) {
        // 任意 token 字符串都会被 mock JwtDecoder 解析为同一份 Jwt
        return request -> {
            request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer fake-token");
            return request;
        };
    }

    private Jwt buildJwt(String role, String userId, String username) {
        java.util.Map<String, Object> claims = new java.util.HashMap<>();
        claims.put("role", role);
        claims.put("username", username);
        claims.put("display_name", username);
        if (!"ADMIN".equals(role)) {
            claims.put("employee_id", "E10001");
        }
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

    @Test
    void unauthenticatedRequestReturns401() throws Exception {
        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.errorCode").value("AUTHENTICATION_REQUIRED"));
    }

    @Test
    void employeeJwtReturns403() throws Exception {
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("EMPLOYEE", "U10001", "zhangsan"));
        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON).with(bearer("EMPLOYEE")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("FORBIDDEN"));
    }

    @Test
    void adminJwtReturns200() throws Exception {
        adminLogBuffer.snapshot(null, null, null, 1);
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("ADMIN", "U90001", "admin"));
        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON).with(bearer("ADMIN")))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.items").isArray())
                .andExpect(jsonPath("$.count").exists());
    }

    /**
     * 通过真实 SecurityConfig 的 oauth2ResourceServer(jwt) → JwtPrincipalConverter，
     * 把 role claim 翻译为 ROLE_<role>：
     *   - ADMIN → ROLE_ADMIN，被 hasRole("ADMIN") 接受
     *   - EMPLOYEE → ROLE_EMPLOYEE，被 hasRole("ADMIN") 拒绝
     */
    @Test
    void principalConverterMapsAdminClaimToRoleAdmin() {
        com.fantuan.copilot.security.JwtPrincipalConverter converter =
                new com.fantuan.copilot.security.JwtPrincipalConverter();
        var auth = converter.convert(buildJwt("ADMIN", "U90001", "admin"));
        assertEquals(1, auth.getAuthorities().size());
        assertTrue(auth.getAuthorities().stream()
                .anyMatch(a -> "ROLE_ADMIN".equals(a.getAuthority())),
                "JwtPrincipalConverter 必须把 ADMIN claim 翻译为 ROLE_ADMIN");
    }

    @Test
    void principalConverterMapsEmployeeClaimToRoleEmployee() {
        com.fantuan.copilot.security.JwtPrincipalConverter converter =
                new com.fantuan.copilot.security.JwtPrincipalConverter();
        var auth = converter.convert(buildJwt("EMPLOYEE", "U10001", "zhangsan"));
        assertTrue(auth.getAuthorities().stream()
                .anyMatch(a -> "ROLE_EMPLOYEE".equals(a.getAuthority())),
                "JwtPrincipalConverter 必须把 EMPLOYEE claim 翻译为 ROLE_EMPLOYEE");
    }

    /**
     * EMPLOYEE 越权访问 /api/admin/logs：
     *   1) 真实过滤链返回 403；
     *   2) SecurityErrorHandlers 在拒绝时真实写入 SECURITY 类别事件到 buffer。
     */
    @Test
    void employeeAccessDeniedRecordsSecurityEvent() throws Exception {
        adminLogBuffer.snapshot(null, null, null, 1);
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("EMPLOYEE", "U10001", "zhangsan"));
        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON).with(bearer("EMPLOYEE")))
                .andExpect(status().isForbidden());

        // ADMIN 再去查 buffer
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("ADMIN", "U90001", "admin"));
        MvcResult result = mockMvc.perform(get("/api/admin/logs")
                        .param("category", AdminLogEvent.CATEGORY_SECURITY)
                        .accept(MediaType.APPLICATION_JSON)
                        .with(bearer("ADMIN")))
                .andExpect(status().isOk())
                .andReturn();

        String body = result.getResponse().getContentAsString();
        assertTrue(body.contains("ADMIN_ACCESS_DENIED"),
                "EMPLOYEE 越权访问 /api/admin/** 必须被 SecurityErrorHandlers 记入 SECURITY 类别: " + body);
        // 不应泄漏用户名明文（userRef 字段为空）
        assertTrue(!body.contains("\"userRef\":\"U10001\""),
                "记录中不应出现 userRef=原始 userId: " + body);
    }

    /**
     * ADMIN 自身查询 /api/admin/logs 不应额外生成 REQUEST 日志：
     * TraceIdFilter 对 /api/admin/** 路径直接跳过，因此 REQUEST 类别为空。
     */
    @Test
    void adminSelfQueryDoesNotGenerateRequestLog() throws Exception {
        adminLogBuffer.snapshot(null, null, null, 1);
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("ADMIN", "U90001", "admin"));

        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON).with(bearer("ADMIN")))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/admin/logs").accept(MediaType.APPLICATION_JSON).with(bearer("ADMIN")))
                .andExpect(status().isOk());

        // 查 REQUEST 类别
        MvcResult result = mockMvc.perform(get("/api/admin/logs")
                        .param("category", AdminLogEvent.CATEGORY_REQUEST)
                        .accept(MediaType.APPLICATION_JSON)
                        .with(bearer("ADMIN")))
                .andExpect(status().isOk())
                .andReturn();
        String body = result.getResponse().getContentAsString();
        // 严格断言：count=0 且 items 数组为空
        assertTrue(body.contains("\"count\":0"),
                "ADMIN 自查询不应产生 REQUEST 日志: " + body);
    }

    @Test
    void invalidFilterReturns400EvenForAdmin() throws Exception {
        when(jwtDecoder.decode(anyString())).thenReturn(buildJwt("ADMIN", "U90001", "admin"));
        mockMvc.perform(get("/api/admin/logs")
                        .param("level", "BOGUS")
                        .accept(MediaType.APPLICATION_JSON)
                        .with(bearer("ADMIN")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("BAD_ADMIN_LOG_FILTER"));
    }
}