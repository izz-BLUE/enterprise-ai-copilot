package com.fantuan.copilot.controller;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VersionController.class)
@AutoConfigureMockMvc(addFilters = false)
class VersionControllerDefaultsTest {

    // WebMvcTest 切片会实例化 Filter 实现（TraceIdFilter），其构造依赖
    // AdminLogBuffer（@Component 不在切片内），此处用 mock 补齐依赖。
    @MockBean
    private AdminLogBuffer adminLogBuffer;

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
