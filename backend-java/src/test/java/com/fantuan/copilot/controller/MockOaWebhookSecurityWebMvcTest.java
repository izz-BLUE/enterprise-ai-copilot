package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.config.SecurityConfig;
import com.fantuan.copilot.security.MockOaWebhookVerifier;
import com.fantuan.copilot.security.SecurityErrorHandlers;
import com.fantuan.copilot.service.action.MockOaWebhookProcessingService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** Verifies that only the exact webhook POST bypasses browser JWT authentication. */
@WebMvcTest(controllers = MockOaWebhookController.class)
@Import({SecurityConfig.class, SecurityErrorHandlers.class, AdminLogBuffer.class})
@TestPropertySource(properties = {
        "auth.jwt.secret=test-secret-test-secret-test-secret-test",
        "auth.jwt.issuer=enterprise-ai-copilot",
        "auth.jwt.audience=enterprise-ai-copilot"
})
class MockOaWebhookSecurityWebMvcTest {
    @Autowired MockMvc mockMvc;

    @MockitoBean JwtEncoder jwtEncoder;
    @MockitoBean JwtDecoder jwtDecoder;
    @MockitoBean com.fantuan.copilot.auth.AppUserDetailsService appUserDetailsService;
    @MockitoBean MockOaWebhookVerifier verifier;
    @MockitoBean MockOaWebhookProcessingService processingService;

    @Test
    void exactWebhookPostIsNotBlockedByBrowserJwtSecurity() throws Exception {
        mockMvc.perform(post(MockOaWebhookController.PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"eventId\":\"evt-1\",\"eventType\":\"EXPENSE_APPROVAL_CHANGED\","
                                + "\"requestId\":\"OA-EXP-1\"}"))
                .andExpect(status().isNoContent());
    }

    @Test
    void otherWebhookMethodsAndPathsRemainProtected() throws Exception {
        mockMvc.perform(get(MockOaWebhookController.PATH))
                .andExpect(status().isUnauthorized());
        mockMvc.perform(post(MockOaWebhookController.PATH + "/other")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnauthorized());
    }
}
