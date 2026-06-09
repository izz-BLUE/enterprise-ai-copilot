package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.AgentChatResponse;
import com.fantuan.copilot.dto.ChatRequest;

import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
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

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    public LangGraphAgentController(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @PostMapping("/api/agent/langgraph/chat")
    public AgentChatResponse langgraphChat(@RequestBody ChatRequest request,
                                           HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] 收到 LangGraph Agent 请求: {}", traceId, request.message());

        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);

            String url = agentBaseUrl + "/agent/langgraph/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<AgentChatResponse> response = restTemplate.postForEntity(
                    url,
                    httpEntity,
                    AgentChatResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            return response.getBody();
        } catch (HttpClientErrorException e) {
            log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
                    traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
            return fallback("Python LangGraph Agent 返回客户端错误: " + e.getMessage(), traceId);
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return fallback("Python Agent 服务调用失败: " + e.getMessage(), traceId);
        }
    }

    private AgentChatResponse fallback(String reason, String traceId) {
        return new AgentChatResponse(
                "当前 Agent 服务暂时不可用，请稍后重试。",
                "error",
                true,
                "error",
                reason,
                List.of(),
                false,
                traceId);
    }
}
