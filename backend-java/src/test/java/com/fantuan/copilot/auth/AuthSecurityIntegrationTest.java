package com.fantuan.copilot.auth;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.controller.AuthController;
import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "business.actions.enabled=true",
        "business.actions.require-admin=true",
        "admin.token=required-admin-token",
        "leave.read.internal-token=internal-test-token"
})
@AutoConfigureMockMvc
class AuthSecurityIntegrationTest extends PostgresIntegrationTestBase {

    private static final String TEST_PASSWORD = "test-" + UUID.randomUUID();

    @Autowired MockMvc mockMvc;
    @Autowired ObjectMapper objectMapper;
    @Autowired JwtDecoder jwtDecoder;
    @Autowired JdbcTemplate jdbc;

    @DynamicPropertySource
    static void authTestProperties(DynamicPropertyRegistry registry) {
        registry.add("demo.auth.default-password", () -> TEST_PASSWORD);
    }

    @AfterEach
    void restoreSeededUsers() {
        jdbc.update("UPDATE app_user SET enabled = TRUE WHERE username IN (?, ?, ?, ?)",
                "zhangsan", "lisi", "wangwu", "admin");
    }

    @Test
    void loginIssuesJwtWithoutEnabledClaimAndInitializerSeedsFourAccounts() throws Exception {
        JsonNode body = login("zhangsan", TEST_PASSWORD);
        assertEquals("U10001", body.get("user").get("userId").asText());
        assertEquals("E10001", body.get("user").get("employeeId").asText());
        assertEquals("EMPLOYEE", body.get("user").get("role").asText());

        Jwt jwt = jwtDecoder.decode(body.get("accessToken").asText());
        assertEquals("U10001", jwt.getSubject());
        assertEquals("E10001", jwt.getClaimAsString("employee_id"));
        assertEquals("EMPLOYEE", jwt.getClaimAsString("role"));
        assertNull(jwt.getClaims().get("enabled"));

        assertEquals(4, jdbc.queryForObject("SELECT COUNT(*) FROM app_user", Integer.class));
        assertEquals(10.0, jdbc.queryForObject(
                "SELECT annual_balance FROM leave_account WHERE employee_id = 'E10001'",
                Double.class));
        assertEquals(5.0, jdbc.queryForObject(
                "SELECT annual_balance FROM leave_account WHERE employee_id = 'E10002'",
                Double.class));
        assertEquals(15.0, jdbc.queryForObject(
                "SELECT annual_balance FROM leave_account WHERE employee_id = 'E10003'",
                Double.class));
        assertEquals(1, jdbc.queryForObject(
                "SELECT COUNT(*) FROM app_user WHERE username = 'admin' AND employee_id IS NULL",
                Integer.class));
    }

    @Test
    void disabledAccountCannotLogin() throws Exception {
        jdbc.update("UPDATE app_user SET enabled = FALSE WHERE username = 'lisi'");

        mockMvc.perform(post("/api/auth/login")
                        .contentType(APPLICATION_JSON)
                        .content("{\"username\":\"lisi\",\"password\":\"" + TEST_PASSWORD + "\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.errorCode").value("AUTHENTICATION_FAILED"));
    }

    @Test
    void validJwtCanReadCurrentUser() throws Exception {
        String token = login("zhangsan", TEST_PASSWORD).get("accessToken").asText();

        mockMvc.perform(get("/api/auth/me").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("U10001"))
                .andExpect(jsonPath("$.employeeId").value("E10001"))
                .andExpect(jsonPath("$.role").value("EMPLOYEE"));
    }

    @Test
    void browserCookieIsHttpOnlyAndWriteRequestsRequireAjaxHeader() throws Exception {
        MvcResult login = mockMvc.perform(post("/api/auth/login")
                        .contentType(APPLICATION_JSON)
                        .content(objectMapper.createObjectNode()
                                .put("username", "zhangsan")
                                .put("password", TEST_PASSWORD)
                                .toString()))
                .andExpect(status().isOk())
                .andReturn();
        String setCookie = login.getResponse().getHeader("Set-Cookie");
        assertTrue(setCookie.contains("HttpOnly"));
        assertTrue(setCookie.contains("SameSite=Strict"));
        Cookie cookie = login.getResponse().getCookie(AuthController.ACCESS_TOKEN_COOKIE);

        mockMvc.perform(get("/api/auth/me").cookie(cookie))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("U10001"));

        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .cookie(cookie)
                        .header("X-Admin-Token", "required-admin-token")
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isUnauthorized());

        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .cookie(cookie)
                        .header("X-Requested-With", "XMLHttpRequest")
                        .header("X-Admin-Token", "required-admin-token")
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isNotFound());

        mockMvc.perform(post("/api/auth/logout")
                        .cookie(cookie)
                        .header("X-Requested-With", "XMLHttpRequest"))
                .andExpect(status().isNoContent())
                .andExpect(result -> assertTrue(
                        result.getResponse().getHeader("Set-Cookie").contains("Max-Age=0")));
    }

    @Test
    void agentRequiresAuthenticationWhenNoJwtIsPresent() throws Exception {
        mockMvc.perform(post("/api/agent/langgraph/chat")
                        .contentType(APPLICATION_JSON)
                        .content("{\"message\":\"几点上班？\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.errorCode").value("AUTHENTICATION_REQUIRED"));
    }

    @Test
    void invalidBearerIsRejected() throws Exception {
        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .header("Authorization", "Bearer invalid-or-expired-token")
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.errorCode").value("AUTHENTICATION_REQUIRED"));
    }

    @Test
    void validJwtCanReachBusinessActionAndAdminCannotActAsEmployee() throws Exception {
        String employeeToken = login("zhangsan", TEST_PASSWORD).get("accessToken").asText();
        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .header("Authorization", "Bearer " + employeeToken)
                        .header("X-Admin-Token", "required-admin-token")
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.errorCode").value("ACTION_NOT_FOUND"));

        String adminToken = login("admin", TEST_PASSWORD).get("accessToken").asText();
        Jwt adminJwt = jwtDecoder.decode(adminToken);
        assertEquals("ADMIN", adminJwt.getClaimAsString("role"));
        assertNull(adminJwt.getClaims().get("employee_id"));
        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("ADMIN_REQUIRED"));

        mockMvc.perform(post("/api/agent/actions/missing-action/cancel")
                        .header("Authorization", "Bearer " + adminToken)
                        .header("X-Admin-Token", "required-admin-token")
                        .header("X-Employee-Id", "E10002")
                        .contentType(APPLICATION_JSON)
                        .content("{\"confirmationNonce\":\"nonce\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("EMPLOYEE_ID_REQUIRED"));
    }

    @Test
    void internalLeaveEndpointUsesOnlyInternalTokenAndDoesNotRequireUserJwt() throws Exception {
        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-test-token")
                        .header("X-Employee-Id", "E10001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.employeeId").value("E10001"))
                .andExpect(jsonPath("$.annualBalance").value(10.0));

        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "internal-test-token")
                        .header("Authorization", "Bearer invalid-proxy-token")
                        .header("X-Employee-Id", "E10001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.employeeId").value("E10001"))
                .andExpect(jsonPath("$.annualBalance").value(10.0));

        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Employee-Id", "E10001"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_FORBIDDEN"));

        mockMvc.perform(get("/api/internal/leave/balance")
                        .header("X-Internal-Token", "wrong-token")
                        .header("X-Employee-Id", "E10001"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.errorCode").value("LEAVE_READ_FORBIDDEN"));
    }

    private JsonNode login(String username, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/auth/login")
                        .contentType(APPLICATION_JSON)
                        .content(objectMapper.createObjectNode()
                                .put("username", username)
                                .put("password", password)
                                .toString()))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode body = objectMapper.readTree(result.getResponse().getContentAsString());
        assertTrue(body.hasNonNull("accessToken"));
        return body;
    }
}
