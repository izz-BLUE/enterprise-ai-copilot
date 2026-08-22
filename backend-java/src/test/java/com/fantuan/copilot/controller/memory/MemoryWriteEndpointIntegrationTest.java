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

    @Test
    void terminalWriteIsRejectedAndNotPersisted() throws Exception {
        String scope = scopeService.issue(USER_ID, CONVERSATION_ID);

        // COMPLETE：Python 写入口禁止终态，返回 409 MEMORY_TERMINAL_NOT_ALLOWED
        mockMvc.perform(post("/api/internal/memory/conversations/{conversationId}/write",
                        CONVERSATION_ID)
                        .header("X-Internal-Token", "internal-test-token")
                        .header("X-Memory-Write-Scope", scope)
                        .contentType(APPLICATION_JSON)
                        .content("""
                                {
                                  "action": "COMPLETE",
                                  "taskType": "LEAVE_REQUEST",
                                  "status": "COMPLETED",
                                  "taskState": {},
                                  "summary": "done"
                                }
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.errorCode", is("MEMORY_TERMINAL_NOT_ALLOWED")));
        org.junit.jupiter.api.Assertions.assertTrue(
                memoryService.find(USER_ID, CONVERSATION_ID).isEmpty(),
                "终态命令被拒后不得落库");

        // ABANDON：同样拒绝且不落库
        mockMvc.perform(post("/api/internal/memory/conversations/{conversationId}/write",
                        CONVERSATION_ID)
                        .header("X-Internal-Token", "internal-test-token")
                        .header("X-Memory-Write-Scope", scope)
                        .contentType(APPLICATION_JSON)
                        .content("""
                                {
                                  "action": "ABANDON",
                                  "taskType": "LEAVE_REQUEST",
                                  "status": "ABANDONED",
                                  "taskState": {},
                                  "summary": "cancelled"
                                }
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.errorCode", is("MEMORY_TERMINAL_NOT_ALLOWED")));
        org.junit.jupiter.api.Assertions.assertTrue(
                memoryService.find(USER_ID, CONVERSATION_ID).isEmpty());

        // UPSERT + COMPLETED 是终态的伪装写法，同样拒绝
        mockMvc.perform(post("/api/internal/memory/conversations/{conversationId}/write",
                        CONVERSATION_ID)
                        .header("X-Internal-Token", "internal-test-token")
                        .header("X-Memory-Write-Scope", scope)
                        .contentType(APPLICATION_JSON)
                        .content("""
                                {
                                  "action": "UPSERT",
                                  "taskType": "LEAVE_REQUEST",
                                  "status": "COMPLETED",
                                  "taskState": {},
                                  "summary": "done"
                                }
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.errorCode", is("MEMORY_TERMINAL_NOT_ALLOWED")));
        org.junit.jupiter.api.Assertions.assertTrue(
                memoryService.find(USER_ID, CONVERSATION_ID).isEmpty());
    }

    @Test
    void nestedLifecycleFieldsAreStrippedAndNotPersisted() throws Exception {
        String scope = scopeService.issue(USER_ID, CONVERSATION_ID);

        // 顶层 action/status 是合法的 UPSERT + ACTIVE；taskState 内嵌生命周期字段
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
                                  "taskState": {
                                    "pending_step": "confirmation",
                                    "status": "COMPLETED",
                                    "lifecycle_state": "ABANDONED",
                                    "nested": {
                                      "status": "COMPLETED",
                                      "kept": 1
                                    }
                                  },
                                  "summary": "等待用户确认"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status", is("ACTIVE")));

        var saved = memoryService.find(USER_ID, CONVERSATION_ID).orElseThrow();
        // 顶层 Memory 状态仍为 ACTIVE（Java 状态机控制）
        org.junit.jupiter.api.Assertions.assertEquals("ACTIVE", saved.status().name());
        // 嵌套生命周期字段不落库：不污染 Read Path / Extractor 上下文
        String taskState = saved.taskStateJson();
        org.junit.jupiter.api.Assertions.assertFalse(taskState.contains("COMPLETED"), taskState);
        org.junit.jupiter.api.Assertions.assertFalse(taskState.contains("ABANDONED"), taskState);
        org.junit.jupiter.api.Assertions.assertFalse(taskState.contains("lifecycle_state"), taskState);
        // 普通业务上下文正常保存
        org.junit.jupiter.api.Assertions.assertTrue(taskState.contains("pending_step"), taskState);
        org.junit.jupiter.api.Assertions.assertTrue(taskState.contains("kept"), taskState);
        org.junit.jupiter.api.Assertions.assertTrue(taskState.contains("confirmation"), taskState);
    }
}
