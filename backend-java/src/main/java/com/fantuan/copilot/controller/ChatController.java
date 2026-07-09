package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

@RestController
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private final RestTemplate restTemplate;

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    public ChatController(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @PostMapping("/api/chat")
    public ChatResponse chat(@Valid @RequestBody ChatRequest request,
                             HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] 收到普通 RAG 请求: {}", traceId, request.message());

        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);

            String url = agentBaseUrl + "/agent/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<ChatResponse> response = restTemplate.postForEntity(
                    url, httpEntity, ChatResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            return response.getBody();
        } catch (HttpClientErrorException e) {
            log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
                    traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
            return new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false
            );
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false
            );
        }
    }
}
