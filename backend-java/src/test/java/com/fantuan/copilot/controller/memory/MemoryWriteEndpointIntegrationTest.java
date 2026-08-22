package com.fantuan.copilot.controller.memory;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import com.fantuan.copilot.service.memory.MemoryWriteScopeService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.is;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 真实 HTTP 路径 + Java Memory service + PostgreSQL 的 ENABLED 写入闭环。 */
@SpringBootTest(properties = "leave.read.internal-token=internal-test-token")
@AutoConfigureMockMvc
class MemoryWriteEndpointIntegrationTest extends PostgresIntegrationTestBase {

    private static final String USER_ID = "U10001";
    private static final String CONVERSATION_ID = "memory-e2e-01";

    @Autowired MockMvc mockMvc;
    @Autowired MemoryWriteScopeService scopeService;
    @Autowired AiTaskMemoryService memoryService;

    @AfterEach
    void cleanUp() {
        memoryService.delete(USER_ID, CONVERSATION_ID);
    }

    @Test
    void signedScopeWritesThroughControllerToPostgres() throws Exception {
        String scope = scopeService.issue(USER_ID, CONVERSATION_ID);

        mockMvc.perform(post("/api/internal/memory/conversations/{conversationId}/write",
                        CONVERSATION_ID)
                        .header("X-Internal-Token", "internal-test-token")
                        .header("X-Memory-Write-Scope", scope)
                        .contentType(APPLICATION_JSON)
                        .content("""
                                {
                                  "action": "UPSERT",
                                  "taskType": "LEAVE_REQUEST",
                                  "status": "ACTIVE",
                                  "taskState": {"waiting_for": "date"},
                                  "summary": "等待补充请假日期"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.action", is("UPSERT")))
                .andExpect(jsonPath("$.taskType", is("LEAVE_REQUEST")))
                .andExpect(jsonPath("$.status", is("ACTIVE")));

        var saved = memoryService.find(USER_ID, CONVERSATION_ID).orElseThrow();
        org.junit.jupiter.api.Assertions.assertEquals("LEAVE_REQUEST", saved.taskType());
        org.junit.jupiter.api.Assertions.assertEquals("ACTIVE", saved.status().name());
        org.junit.jupiter.api.Assertions.assertTrue(saved.taskStateJson().contains("waiting_for"));
    }
}
