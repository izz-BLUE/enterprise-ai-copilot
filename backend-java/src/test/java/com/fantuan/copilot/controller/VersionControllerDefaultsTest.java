package com.fantuan.copilot.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VersionController.class)
@AutoConfigureMockMvc(addFilters = false)
class VersionControllerDefaultsTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void returnsSafeDefaults() throws Exception {
        mockMvc.perform(get("/api/version"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.service").value("backend-java"))
                .andExpect(jsonPath("$.version").value("dev"))
                .andExpect(jsonPath("$.gitCommit").value("unknown"))
                .andExpect(jsonPath("$.buildTime").value("unknown"));
    }
}
