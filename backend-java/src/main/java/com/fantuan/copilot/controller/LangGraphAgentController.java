package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
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

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    @Value("${admin.token:}")
    private String adminToken;

    public LangGraphAgentController(RestTemplate restTemplate, PythonAgentBulkhead pythonAgentBulkhead) {
        this.restTemplate = restTemplate;
        this.pythonAgentBulkhead = pythonAgentBulkhead;
    }

    /**
     * 判断本次请求是否允许 eval 路由。
     *
     * 规则：
     * - admin.token 为空 → Demo 模式，允许 eval（不代表真实管理员认证）
     * - admin.token 非空且 X-Admin-Token 匹配 → 允许 eval
     * - admin.token 非空且 X-Admin-Token 缺失/不匹配 → 不允许 eval
     */
    private boolean isEvalAllowed(HttpServletRequest request) {
        if (adminToken == null || adminToken.isBlank()) {
            return true; // Demo 模式，零配置允许 eval
        }
        String requestToken = request.getHeader("X-Admin-Token");
        return adminToken.equals(requestToken);
    }

    @PostMapping("/api/agent/langgraph/chat")
    public ResponseEntity<AgentChatResponse> langgraphChat(@Valid @RequestBody ChatRequest request,
                                                           HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        boolean allowEval = isEvalAllowed(httpRequest);
        log.info("[{}] 收到 LangGraph Agent 请求: {}, allowEval={}", traceId, request.message(), allowEval);

        PythonAgentBulkhead.Permit permit = pythonAgentBulkhead.tryAcquire(traceId);
        if (permit == null) {
            return busy(traceId);
        }

        try (permit) {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            headers.set("X-Allow-Eval", String.valueOf(allowEval));
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);

            String url = agentBaseUrl + "/agent/langgraph/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<AgentChatResponse> response = restTemplate.postForEntity(
                    url,
                    httpEntity,
                    AgentChatResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            return ResponseEntity.ok(response.getBody());
        } catch (HttpClientErrorException e) {
            if (e.getStatusCode().value() == HttpStatus.TOO_MANY_REQUESTS.value()) {
                log.warn("[{}] Python 并发已满", traceId);
                return busy(traceId);
            }
            log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
                    traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
            return ResponseEntity.ok(fallback(traceId));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return ResponseEntity.ok(fallback(traceId));
        }
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
}
