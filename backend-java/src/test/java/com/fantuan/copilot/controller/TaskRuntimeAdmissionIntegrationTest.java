package com.fantuan.copilot.controller;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.task.TaskDecompositionResponse;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.IdentityContext;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.memory.AiTaskMemory;
import com.fantuan.copilot.model.memory.TaskStatus;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class TaskRuntimeAdmissionIntegrationTest extends PostgresIntegrationTestBase {
    private static final VerifiedIdentity USER = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三",
            AuthRole.EMPLOYEE, true, VerifiedIdentity.Source.JWT);
    private static final String CONVERSATION = "legacy-admission";
    private static final String COMPOSITE = "帮我请假，然后把最近一次出差报销。";

    @Autowired LangGraphAgentController controller;
    @Autowired BusinessActionService actionService;
    @Autowired PendingActionRepository actions;
    @Autowired AiTaskMemoryService memoryService;
    @Autowired JdbcTemplate jdbc;

    @MockitoBean PythonAgentGateway pythonAgentGateway;
    @MockitoBean IdentityContext identityContext;

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.update("UPDATE leave_account SET annual_balance = 5.0 WHERE employee_id = 'E10001'");
        when(identityContext.require(any())).thenReturn(USER);
    }

    @Test
    void legacyWaitingUserBlocksCompositeBeforeDecompositionWithoutChangingState() {
        PendingActionView pending = actionService.createPending(
                leaveProposal(), "legacy-wait", null, USER, CONVERSATION);
        memoryService.upsert(USER.userId(), CONVERSATION, "LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{\"step\":\"waiting_confirmation\"}", "等待确认");
        PendingAction actionBefore = actions.find(pending.actionId()).orElseThrow();
        AiTaskMemory memoryBefore = memoryService.find(USER.userId(), CONVERSATION).orElseThrow();

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest(COMPOSITE, CONVERSATION), request("legacy-block"));

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertNotNull(response.getBody());
        assertFalse(response.getBody().success());
        assertEquals("当前会话已有待确认的申请，请先确认或取消后再发起新申请。",
                response.getBody().answer());
        assertEquals(0, count("task_execution"));
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(DISTINCT task_group_id) FROM task_execution", Integer.class));
        assertEquals(1, count("business_action"));

        PendingAction actionAfter = actions.find(pending.actionId()).orElseThrow();
        assertEquals(actionBefore.actionId(), actionAfter.actionId());
        assertEquals(actionBefore.status(), actionAfter.status());
        assertEquals(actionBefore.expiresAt(), actionAfter.expiresAt());
        assertEquals(actionBefore.completedAt(), actionAfter.completedAt());
        assertEquals(actionBefore.requestId(), actionAfter.requestId());
        assertArrayEquals(actionBefore.confirmationNonceDigest(),
                actionAfter.confirmationNonceDigest());

        AiTaskMemory memoryAfter = memoryService.find(USER.userId(), CONVERSATION).orElseThrow();
        assertEquals(memoryBefore.status(), memoryAfter.status());
        assertEquals(memoryBefore.taskType(), memoryAfter.taskType());
        assertEquals(memoryBefore.taskStateJson(), memoryAfter.taskStateJson());
        assertEquals(memoryBefore.summary(), memoryAfter.summary());
        assertEquals(memoryBefore.updatedAt(), memoryAfter.updatedAt());

        verify(pythonAgentGateway, never()).post(eq("/agent/tasks/decompose"), any(), any(),
                eq(TaskDecompositionResponse.class), anyString());
        verify(pythonAgentGateway, never()).post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), anyString());
    }

    @Test
    void expiredLegacyWaitReconcilesBeforeCompositeAdmissionContinues() {
        PendingActionView pending = actionService.createPending(
                leaveProposal(), "legacy-expired", null, USER, CONVERSATION);
        memoryService.upsert(USER.userId(), CONVERSATION, "LEAVE_REQUEST",
                TaskStatus.ACTIVE, "{\"step\":\"waiting_confirmation\"}", "等待确认");
        jdbc.update("UPDATE business_action SET expires_at = NOW() - INTERVAL '1 second' "
                + "WHERE action_id = ?", pending.actionId());

        when(pythonAgentGateway.post(eq("/agent/tasks/decompose"), any(), any(),
                eq(TaskDecompositionResponse.class), anyString())).thenReturn(
                new TaskDecompositionResponse("multi", List.of(
                        new TaskDecompositionResponse.TaskSpec(
                                "LEAVE_REQUEST", "帮我请假", 1),
                        new TaskDecompositionResponse.TaskSpec(
                                "EXPENSE_CLAIM", "把最近一次出差报销。", 2)), ""));
        when(pythonAgentGateway.post(eq("/agent/langgraph/chat"), any(), any(),
                eq(PythonAgentResponse.class), anyString())).thenReturn(
                new PythonAgentResponse("请补充请假日期", "action", true,
                        "business_action", "", List.of(), true, "python-trace",
                        null, List.of("start_date"), null));

        ResponseEntity<AgentChatResponse> response = controller.langgraphChat(
                new ChatRequest(COMPOSITE, CONVERSATION), request("legacy-expired-chat"));

        assertEquals(HttpStatus.OK, response.getStatusCode());
        assertEquals(ActionStatus.EXPIRED,
                actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(TaskStatus.ABANDONED,
                memoryService.find(USER.userId(), CONVERSATION).orElseThrow().status());
        assertEquals(2, count("task_execution"));
        assertEquals(1, jdbc.queryForObject(
                "SELECT COUNT(DISTINCT task_group_id) FROM task_execution", Integer.class));
        assertEquals(List.of("WAITING_CLARIFICATION", "PENDING"), jdbc.queryForList(
                "SELECT status FROM task_execution ORDER BY sequence_no", String.class));
        verify(pythonAgentGateway).post(eq("/agent/tasks/decompose"), any(), any(),
                eq(TaskDecompositionResponse.class), anyString());
    }

    private AnnualLeaveActionProposal leaveProposal() {
        LocalDate date = actionService.businessDate().plusDays(2);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return new AnnualLeaveActionProposal(BusinessActionType.ANNUAL_LEAVE_REQUEST,
                date, date, "admission test", HalfDay.NONE);
    }

    private HttpServletRequest request(String traceId) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn(traceId);
        return request;
    }

    private int count(String table) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }
}
