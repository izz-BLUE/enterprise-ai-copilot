package com.fantuan.copilot.controller.admin;

import com.fantuan.copilot.adminlog.AdminLogBuffer;
import com.fantuan.copilot.adminlog.AdminLogEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import jakarta.servlet.http.HttpServletRequest;

import java.time.Instant;

import static org.mockito.Mockito.when;
import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class AdminLogControllerTest {

    private AdminLogBuffer buffer;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        buffer = new AdminLogBuffer();
        // 预填 3 条样本
        buffer.record(new AdminLogEvent(
                "id-1", Instant.parse("2026-08-23T10:00:00Z"),
                AdminLogEvent.LEVEL_INFO, AdminLogEvent.CATEGORY_AGENT,
                "AGENT_REQUEST_RECEIVED", "trace-1",
                AdminLogEvent.SERVICE, "user-1", null,
                null, null, 12L, "msg-1", null, null, null));
        buffer.record(new AdminLogEvent(
                "id-2", Instant.parse("2026-08-23T10:01:00Z"),
                AdminLogEvent.LEVEL_ERROR, AdminLogEvent.CATEGORY_MEMORY,
                "MEMORY_WRITE_REJECTED", "trace-2",
                AdminLogEvent.SERVICE, null, null,
                null, "scope_invalid", null, "msg-2", null, null, null));
        buffer.record(new AdminLogEvent(
                "id-3", Instant.parse("2026-08-23T10:02:00Z"),
                AdminLogEvent.LEVEL_WARN, AdminLogEvent.CATEGORY_SECURITY,
                "ADMIN_ACCESS_DENIED", "trace-3",
                AdminLogEvent.SERVICE, null, null,
                null, "403", null, "denied", null, null, null));

        mockMvc = MockMvcBuilders
                .standaloneSetup(new AdminLogController(buffer))
                .build();
    }

    @Test
    void getLogsReturnsItemsAndCount() throws Exception {
        mockMvc.perform(get("/api/admin/logs")
                        .requestAttr("traceId", "test-trace"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.count").value(3))
                .andExpect(jsonPath("$.total").value(3))
                .andExpect(jsonPath("$.hasMore").value(false))
                .andExpect(jsonPath("$.items[0].event").value("ADMIN_ACCESS_DENIED"))
                .andExpect(jsonPath("$.items[1].event").value("MEMORY_WRITE_REJECTED"))
                .andExpect(jsonPath("$.items[2].event").value("AGENT_REQUEST_RECEIVED"));
    }

    @Test
    void filterByLevelReturnsOnlyMatching() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("level", "ERROR"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(1))
                .andExpect(jsonPath("$.items[0].event").value("MEMORY_WRITE_REJECTED"));
    }

    @Test
    void paginationReturnsSecondPageMetadata() throws Exception {
        mockMvc.perform(get("/api/admin/logs")
                        .param("limit", "2")
                        .param("offset", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(1))
                .andExpect(jsonPath("$.total").value(3))
                .andExpect(jsonPath("$.offset").value(2))
                .andExpect(jsonPath("$.hasMore").value(false))
                .andExpect(jsonPath("$.items[0].event").value("AGENT_REQUEST_RECEIVED"));
    }

    @Test
    void filterByCategoryReturnsOnlyMatching() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("category", "SECURITY"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(1))
                .andExpect(jsonPath("$.items[0].event").value("ADMIN_ACCESS_DENIED"));
    }

    @Test
    void filterByTraceIdReturnsOnlyMatching() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("traceId", "trace-2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(1))
                .andExpect(jsonPath("$.items[0].traceId").value("trace-2"));
    }

    @Test
    void invalidLevelReturns400() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("level", "BOGUS"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("BAD_ADMIN_LOG_FILTER"));
    }

    @Test
    void invalidCategoryReturns400() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("category", "BOGUS"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("BAD_ADMIN_LOG_FILTER"));
    }

    @Test
    void invalidLimitReturns400() throws Exception {
        mockMvc.perform(get("/api/admin/logs").param("limit", "500"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errorCode").value("BAD_ADMIN_LOG_FILTER"));
    }

    @Test
    void limitDefaultsTo50() throws Exception {
        // 把 buffer 填到 100 条
        for (int i = 0; i < 100; i++) {
            buffer.record(new AdminLogEvent(
                    "id-x" + i, Instant.ofEpochMilli(i),
                    AdminLogEvent.LEVEL_INFO, AdminLogEvent.CATEGORY_SYSTEM,
                    "X", null, AdminLogEvent.SERVICE,
                    null, null, null, null, null, "m", null, null, null));
        }
        mockMvc.perform(get("/api/admin/logs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(50));
    }

    @Test
    void requestLogIncludesHttpMethodPathAndStatus() throws Exception {
        buffer.record(new AdminLogEvent(
                "id-req-1", Instant.parse("2026-08-23T15:30:12Z"),
                AdminLogEvent.LEVEL_INFO, AdminLogEvent.CATEGORY_REQUEST,
                "REQUEST_COMPLETED", "846c680b-3011-4ee2-a179-e0b311c9dfc3",
                AdminLogEvent.SERVICE,
                null, null,
                null,                 // statusFrom  ← REQUEST 类别强制 null
                null,                 // statusTo    ← REQUEST 类别强制 null
                128L,
                null,
                "POST",
                "/api/agent/actions/{id}/confirm",
                200));
        org.springframework.test.web.servlet.MvcResult result = mockMvc.perform(get("/api/admin/logs"))
                .andExpect(status().isOk())
                .andReturn();
        String body = result.getResponse().getContentAsString();
        // 字段都必须出现且规范化路径不能含实际 id / nonce
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("\"httpMethod\":\"POST\""));
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("\"path\":\"/api/agent/actions/{id}/confirm\""));
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("\"httpStatus\":200"));
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("\"traceId\":\"846c680b-3011-4ee2-a179-e0b311c9dfc3\""));
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("\"durationMs\":128"));
        org.junit.jupiter.api.Assertions.assertTrue(body.contains("REQUEST_COMPLETED"));
        // REQUEST 类别不应用 statusTo 字段重复响应码
        org.junit.jupiter.api.Assertions.assertTrue(!body.contains("\"statusTo\":\"200\""));
    }
}
