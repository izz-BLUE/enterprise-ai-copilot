package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.PythonAgentResponse;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.service.AdminAccessService;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionService;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import java.util.List;

@RestController
public class LangGraphAgentController {

    private static final Logger log = LoggerFactory.getLogger(LangGraphAgentController.class);
    private final RestTemplate restTemplate;
    private final PythonAgentBulkhead pythonAgentBulkhead;
    private final AdminAccessService adminAccessService;
    private final BusinessActionService businessActionService;

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    public LangGraphAgentController(RestTemplate restTemplate, PythonAgentBulkhead pythonAgentBulkhead) {
        this(restTemplate, pythonAgentBulkhead, new AdminAccessService(""), null);
    }

    @Autowired
    public LangGraphAgentController(RestTemplate restTemplate,
                                    PythonAgentBulkhead pythonAgentBulkhead,
                                    AdminAccessService adminAccessService,
                                    BusinessActionService businessActionService) {
        this.restTemplate = restTemplate;
        this.pythonAgentBulkhead = pythonAgentBulkhead;
        this.adminAccessService = adminAccessService;
        this.businessActionService = businessActionService;
    }

    /**
     * 判断本次请求是否允许 eval 路由。
     *
     * 规则：
     * - admin.token 为空 → Demo 模式，允许 eval（不代表真实管理员认证）
     * - admin.token 非空且 X-Admin-Token 匹配 → 允许 eval
     * - admin.token 非空且 X-Admin-Token 缺失/不匹配 → 不允许 eval
     */
    @PostMapping("/api/agent/langgraph/chat")
    public ResponseEntity<AgentChatResponse> langgraphChat(@Valid @RequestBody ChatRequest request,
                                                           HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        String presentedToken = httpRequest.getHeader("X-Admin-Token");
        boolean allowEval = adminAccessService.isAdmin(presentedToken);
        boolean allowBusinessActions = businessActionService != null
                && businessActionService.isAllowed(presentedToken);
        log.info("[{}] 收到 LangGraph Agent 请求: allowEval={}, allowBusinessActions={}",
                traceId, allowEval, allowBusinessActions);

        PythonAgentBulkhead.Permit permit = pythonAgentBulkhead.tryAcquire(traceId);
        if (permit == null) {
            return busy(traceId);
        }

        PythonAgentResponse pythonResponse;
        try (permit) {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            headers.set("X-Allow-Eval", String.valueOf(allowEval));
            headers.set("X-Allow-Business-Actions", String.valueOf(allowBusinessActions));
            if (businessActionService != null) {
                headers.set("X-Business-Date", businessActionService.businessDate().toString());
            }
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);

            String url = agentBaseUrl + "/agent/langgraph/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<PythonAgentResponse> response = restTemplate.postForEntity(
                    url,
                    httpEntity,
                    PythonAgentResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            pythonResponse = response.getBody();
        } catch (HttpClientErrorException e) {
            if (e.getStatusCode().value() == HttpStatus.TOO_MANY_REQUESTS.value()) {
                log.warn("[{}] Python 并发已满", traceId);
                return busy(traceId);
            }
            log.error("[{}] Python 返回 HTTP 4xx: status={}", traceId, e.getStatusCode());
            return ResponseEntity.ok(fallback(traceId));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return ResponseEntity.ok(fallback(traceId));
        }

        if (pythonResponse == null) {
            return ResponseEntity.ok(fallback(traceId));
        }
        PendingActionView pendingAction = null;
        if (pythonResponse.actionProposal() != null) {
            if (!allowBusinessActions) {
                return safeActionFailure(traceId, "业务动作功能未启用或当前请求无权限。");
            }
            try {
                pendingAction = businessActionService.createPending(
                        pythonResponse.actionProposal(), traceId, presentedToken);
            } catch (ActionException exception) {
                log.warn("[{}] Python Proposal未创建 PendingAction: code={}",
                        traceId, exception.errorCode());
                return safeActionFailure(traceId, "暂时无法生成申请草稿，请检查信息后重试。");
            } catch (RuntimeException exception) {
                log.error("[{}] PendingAction持久化失败", traceId);
                return safeActionFailure(traceId, "业务动作处理失败，请稍后重试。");
            }
        }
        AgentChatResponse publicResponse = AgentChatResponse.fromPython(pythonResponse, pendingAction);
        ResponseEntity.BodyBuilder builder = ResponseEntity.ok();
        if (pendingAction != null) {
            builder.cacheControl(org.springframework.http.CacheControl.noStore());
        }
        return builder.body(publicResponse);
    }

    private ResponseEntity<AgentChatResponse> busy(String traceId) {
        AgentChatResponse response = new AgentChatResponse(
                "当前请求较多，请稍后重试。",
                "busy",
                true,
                "overloaded",
                "",
                List.of(),
                false,
                traceId
        );
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, "1")
                .body(response);
    }

    private AgentChatResponse fallback(String traceId) {
        return new AgentChatResponse(
                "当前 Agent 服务暂时不可用，请稍后重试。",
                "error",
                true,
                "error",
                "",  // reason 不暴露异常细节
                List.of(),
                false,
                traceId);
    }

    private ResponseEntity<AgentChatResponse> safeActionFailure(String traceId, String message) {
        AgentChatResponse response = new AgentChatResponse(message, "error", true,
                "business_action", "", List.of(), false, traceId);
        return ResponseEntity.ok()
                .cacheControl(org.springframework.http.CacheControl.noStore())
                .body(response);
    }
}
