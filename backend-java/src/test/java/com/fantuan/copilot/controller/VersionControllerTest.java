package com.fantuan.copilot.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VersionController.class)
@AutoConfigureMockMvc(addFilters = false)
@TestPropertySource(properties = {
        "APP_VERSION=0.4.1-test",
        "GIT_COMMIT=0123456789012345678901234567890123456789",
        "BUILD_TIME=2026-07-15T06:30:00Z"
})
class VersionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void returnsConfiguredVersionWithoutSensitiveFields() throws Exception {
        mockMvc.perform(get("/api/version"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.service").value("backend-java"))
                .andExpect(jsonPath("$.version").value("0.4.1-test"))
                .andExpect(jsonPath("$.gitCommit").value("0123456789012345678901234567890123456789"))
                .andExpect(jsonPath("$.buildTime").value("2026-07-15T06:30:00Z"))
                .andExpect(jsonPath("$.ADMIN_TOKEN").doesNotExist())
                .andExpect(jsonPath("$.DEEPSEEK_API_KEY").doesNotExist());
    }
}
