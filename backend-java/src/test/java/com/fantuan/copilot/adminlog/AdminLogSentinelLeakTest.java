package com.fantuan.copilot.adminlog;

import com.fantuan.copilot.controller.LangGraphAgentController;
import com.fantuan.copilot.controller.LangGraphAgentControllerTestFactory;
import com.fantuan.copilot.controller.admin.AdminLogController;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.auth.AuthenticatedUser;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.leave.LeaveExecutionGateway;
import com.fantuan.copilot.gateway.leave.LeaveExecutionResult;
import com.fantuan.copilot.gateway.leave.LeaveSubmission;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionNonceService;
import com.fantuan.copilot.service.action.BusinessActionHandlerRegistry;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import com.fantuan.copilot.service.action.BusinessActionService;
import com.fantuan.copilot.service.memory.AiTaskMemoryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.client.RestTemplate;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 真实敏感值哨兵泄漏验证 —— 每个哨兵都先真实进入被测输入对象，再扫描
 * AdminLogBuffer 与 /api/admin/logs 返回 JSON，断言哨兵字符串不出现。
 *
 * 输入路径映射（每条断言都有真实输入）：
 *   - SENSITIVE_USER_QUESTION_123      → ChatRequest.message → LangGraphAgentController.langgraphChat（真实方法）
 *   - SENSITIVE_AGENT_ANSWER_456       → PythonAgentResponse.answer（同上，mock Python 响应）
 *   - SENSITIVE_LEAVE_REASON_GHI       → AnnualLeaveActionProposal.reason → PendingAction.reason → BusinessActionService.audit（审计）
 *   - SENSITIVE_CONFIRMATION_NONCE_789 → PendingAction.actionId（业务动作引用字段，经 auditRef 哈希）
 *   - SENSITIVE_TASK_STATE_DEF         → PythonAgentResponse.memoryProposal → Java 持久化旁路
 *
 * 日志模型自身允许存在的预定义 message（如 "Business action succeeded"）允许出现。
 */
class AdminLogSentinelLeakTest {

    private static final String SENTINEL_USER_QUESTION = "SENSITIVE_USER_QUESTION_123";
    private static final String SENTINEL_AGENT_ANSWER = "SENSITIVE_AGENT_ANSWER_456";
    private static final String SENTINEL_NONCE = "SENSITIVE_CONFIRMATION_NONCE_789";
    private static final String SENTINEL_SCOPE = "SENSITIVE_MEMORY_SCOPE_ABC";
    private static final String SENTINEL_TASK_STATE = "SENSITIVE_TASK_STATE_DEF";
    private static final String SENTINEL_LEAVE_REASON = "SENSITIVE_LEAVE_REASON_GHI";

    private static final List<String> ALL_SENTINELS = List.of(
            SENTINEL_USER_QUESTION,
            SENTINEL_AGENT_ANSWER,
            SENTINEL_NONCE,
            SENTINEL_SCOPE,
            SENTINEL_TASK_STATE,
            SENTINEL_LEAVE_REASON);

    private static final Clock FIXED_CLOCK = Clock.fixed(
            Instant.parse("2026-08-23T10:00:00Z"),
            java.time.ZoneId.of("Asia/Shanghai"));

    private AdminLogBuffer buffer;
    private AdminLogController controller;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        buffer = new AdminLogBuffer();
        controller = new AdminLogController(buffer);
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    /**
     * 输入路径：SENTINEL_USER_QUESTION 进入 Agent 请求 message；
     *           SENTINEL_AGENT_ANSWER 进入 Python 响应 answer。
     * 真实调用 LangGraphAgentController.langgraphChat（mock 外部 Python 调用）。
     */
    @Test
    void langGraphAgentPathNeverLeaksQuestionOrAnswer() throws Exception {
        RestTemplate restTemplate = mock(RestTemplate.class);
        PythonAgentResponse pythonResponse = new PythonAgentResponse(
                SENTINEL_AGENT_ANSWER,   // answer 含哨兵
                "rag", true, "rag", "",
                List.of(), true, "py-trace", null, List.of(),
                new AgentMemoryProposal("GENERIC",
                        Map.of("note", SENTINEL_TASK_STATE), SENTINEL_AGENT_ANSWER));
        when(restTemplate.postForEntity(anyString(), any(), eq(PythonAgentResponse.class)))
                .thenReturn(ResponseEntity.ok(pythonResponse));

        AuthenticatedUser user = new AuthenticatedUser(
                "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true);
        SecurityContextHolder.getContext().setAuthentication(
                UsernamePasswordAuthenticationToken.authenticated(
                        user, null, List.of(new SimpleGrantedAuthority("ROLE_EMPLOYEE"))));

        AdminAccessService adminAccessService = mock(AdminAccessService.class);
        when(adminAccessService.isAdminIdentity(any())).thenReturn(false);

        BusinessActionService businessActionService = mock(BusinessActionService.class);
        when(businessActionService.isAllowed(any(), any(com.fantuan.copilot.identity.VerifiedIdentity.class)))
                .thenReturn(false);
        when(businessActionService.businessDate()).thenReturn(LocalDate.of(2026, 8, 23));

        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(memoryService.find(anyString(), anyString())).thenReturn(Optional.empty());

        // 唯一构造器注入测试的 AdminLogBuffer；生产代码已无自行 new buffer 的兼容路径。
        LangGraphAgentController agentController = LangGraphAgentControllerTestFactory.create(
                new PythonAgentGateway(restTemplate,
                        new com.fantuan.copilot.concurrency.PythonAgentBulkhead(3, 500),
                        "http://python-agent"),
                adminAccessService,
                businessActionService,
                new com.fantuan.copilot.identity.IdentityContext(),
                memoryService,
                buffer);

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/agent/langgraph/chat");
        request.setAttribute("traceId", "trace-agent-1");

        agentController.langgraphChat(
                new com.fantuan.copilot.dto.ChatRequest(SENTINEL_USER_QUESTION, null),
                request);

        String body = snapshotAll();
        // 正常应有 AGENT_REQUEST_RECEIVED / COMPLETED 事件
        assertTrue(body.contains("AGENT_REQUEST_RECEIVED"), body);
        assertTrue(body.contains("AGENT_REQUEST_COMPLETED"), body);
        // 用户问题与 Python 回答都不应进入日志
        for (String s : ALL_SENTINELS) {
            assertFalse(body.contains(s),
                    "Agent 路径不应泄漏哨兵 [" + s + "]，输出：" + body);
        }
    }

    /**
     * 输入路径：SENTINEL_LEAVE_REASON 进入 PendingAction.reason；
     *           SENTINEL_NONCE 进入 actionId / displayName 等业务引用字段。
     * 真实调用 BusinessActionService.audit → emitAdminLog。
     */
    @Test
    void businessActionAuditNeverLeaksSentinels() throws Exception {
        BusinessActionService service = newBusinessServiceWithMockDeps();

        java.lang.reflect.Method audit = BusinessActionService.class.getDeclaredMethod(
                "audit", String.class, PendingAction.class,
                ActionStatus.class, ActionStatus.class,
                String.class, String.class, Instant.class);
        audit.setAccessible(true);

        PendingAction action = PendingAction.pending(
                "act_" + SENTINEL_NONCE,                       // actionId 含哨兵
                BusinessActionType.ANNUAL_LEAVE_REQUEST,
                "trace-audit",
                "U_" + SENTINEL_USER_QUESTION,                 // userId 含哨兵
                "conv-" + SENTINEL_SCOPE,                      // conversationId 含哨兵
                "U_" + SENTINEL_USER_QUESTION,
                "Demo " + SENTINEL_AGENT_ANSWER,               // displayName 含哨兵
                LocalDate.of(2026, 8, 25),
                LocalDate.of(2026, 8, 25),
                HalfDay.NONE,
                SENTINEL_LEAVE_REASON,                          // reason 含哨兵
                new BigDecimal("1.0"),
                new BigDecimal("10.0"),
                new BigDecimal("9.0"),
                new byte[32],
                FIXED_CLOCK.instant(),
                FIXED_CLOCK.instant().plusSeconds(3600),
                null); // action_payload_json: P2-A V6 新增
        audit.invoke(service, "trace-audit", action,
                ActionStatus.PROCESSING, ActionStatus.SUCCEEDED,
                "ACTION_SUCCEEDED", "REQ-x", FIXED_CLOCK.instant());

        String body = snapshotAll();
        assertTrue(body.contains("ACTION_SUCCEEDED"), body);
        assertTrue(body.contains("Business action succeeded"), body);
        for (String s : ALL_SENTINELS) {
            assertFalse(body.contains(s),
                    "audit 路径不应泄漏哨兵 [" + s + "]，输出：" + body);
        }
    }

    /**
     * 输入路径：SENTINEL_NONCE / SENTINEL_SCOPE 进入 URL path 与 query string。
     * 真实调用 TraceIdFilter.normalizePath（包私有静态，反射访问）。
     */
    @Test
    void requestLogNormalizePathNeverLeaksSentinels() throws Exception {
        com.fantuan.copilot.filter.TraceIdFilter filter =
                new com.fantuan.copilot.filter.TraceIdFilter(buffer);
        java.lang.reflect.Method normalize =
                com.fantuan.copilot.filter.TraceIdFilter.class.getDeclaredMethod(
                        "normalizePath", String.class);
        normalize.setAccessible(true);
        String normalized = (String) normalize.invoke(filter,
                "/api/agent/actions/act_" + SENTINEL_NONCE + "/confirm?foo=" + SENTINEL_SCOPE);
        assertFalse(normalized.contains(SENTINEL_NONCE), "normalizePath 不应保留 actionId 原文");
        assertFalse(normalized.contains(SENTINEL_SCOPE), "normalizePath 不应保留 query string");
        assertTrue(normalized.contains("{id}"), "normalizePath 应替换 actionId 为 {id}");
        assertFalse(normalized.contains("?"), "normalizePath 应剥离 query string");
    }

    /**
     * 输入路径：合法预定义 message 写入 buffer（应保留，不被误判为泄漏）。
     */
    @Test
    void snapshotJsonOverHttpApiKeepsPredefinedMessage() throws Exception {
        buffer.record(new AdminLogEvent(
                "id-1", Instant.now(),
                AdminLogEvent.LEVEL_INFO, AdminLogEvent.CATEGORY_BUSINESS_ACTION,
                "ACTION_SUCCEEDED", "trace-api",
                AdminLogEvent.SERVICE,
                null, null,
                "PROCESSING", "SUCCEEDED",
                10L,
                "Business action succeeded",
                null, null, null));

        MvcResult result = mockMvc.perform(get("/api/admin/logs"))
                .andExpect(status().isOk())
                .andReturn();
        String body = result.getResponse().getContentAsString();
        assertTrue(body.contains("Business action succeeded"),
                "合法预定义 message 应保留：" + body);
        for (String s : ALL_SENTINELS) {
            assertFalse(body.contains(s),
                    "/api/admin/logs 不应回显敏感哨兵 [" + s + "]");
        }
    }

    private String snapshotAll() throws Exception {
        MvcResult result = mockMvc.perform(get("/api/admin/logs"))
                .andExpect(status().isOk())
                .andReturn();
        return result.getResponse().getContentAsString();
    }

    private BusinessActionService newBusinessServiceWithMockDeps() {
        BusinessActionProperties props = new BusinessActionProperties();
        props.setEnabled(true);
        props.setRequireAdmin(false);
        PendingActionRepository actions = mock(PendingActionRepository.class);
        LeaveAccountRepository accounts = mock(LeaveAccountRepository.class);
        LeaveExecutionGateway gateway = mock(LeaveExecutionGateway.class);
        AiTaskMemoryService memoryService = mock(AiTaskMemoryService.class);
        when(actions.findExpired(any())).thenReturn(List.of());
        when(actions.expirePending(any())).thenReturn(0);
        when(actions.countActive()).thenReturn(0);
        when(accounts.findBalanceForUpdate(anyString()))
                .thenReturn(Optional.of(new BigDecimal("10.0")));
        when(gateway.hasConflict(anyString(), any(), any())).thenReturn(false);
        when(gateway.submit(any(LeaveSubmission.class)))
                .thenReturn(new LeaveExecutionResult("REQ-x", Instant.now()));
        // V2 §十七: Service 依赖 HandlerRegistry；业务 handler 在其它测试验证。
        BusinessActionHandlerRegistry registry = new BusinessActionHandlerRegistry(
                List.of(new com.fantuan.copilot.service.action.handler.AnnualLeaveActionHandler(
                        accounts, gateway)));
        return new BusinessActionService(
                props,
                new AdminAccessService(""),
                actions, registry,
                new ActionNonceService(), memoryService,
                buffer,
                FIXED_CLOCK,
                mock(com.fantuan.copilot.service.task.TaskRuntimeService.class));
    }
}
