package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
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

import java.util.UUID;

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
    public ChatResponse chat(@RequestBody ChatRequest request) {
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);
            String url = agentBaseUrl + "/agent/chat";
            ResponseEntity<ChatResponse> response = restTemplate.postForEntity(
                    url, httpEntity, ChatResponse.class);
            return response.getBody();
        } catch (HttpClientErrorException e) {
            log.error("调用 Python Agent 返回 HTTP 4xx: status={}, body={}", e.getStatusCode(), e.getResponseBodyAsString(), e);
            return new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    UUID.randomUUID().toString(),
                    false
            );
        } catch (Exception e) {
            log.error("调用 Python Agent 发生未知异常", e);
            return new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    UUID.randomUUID().toString(),
                    false
            );
        }
    }
}