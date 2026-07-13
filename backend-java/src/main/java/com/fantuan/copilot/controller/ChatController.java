package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
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
    private final PythonAgentBulkhead pythonAgentBulkhead;

    @Value("${python.agent.base-url}")
    private String agentBaseUrl;

    public ChatController(RestTemplate restTemplate, PythonAgentBulkhead pythonAgentBulkhead) {
        this.restTemplate = restTemplate;
        this.pythonAgentBulkhead = pythonAgentBulkhead;
    }

    @PostMapping("/api/chat")
    public ResponseEntity<ChatResponse> chat(@Valid @RequestBody ChatRequest request,
                                             HttpServletRequest httpRequest) {
        String traceId = (String) httpRequest.getAttribute("traceId");
        log.info("[{}] 收到普通 RAG 请求: {}", traceId, request.message());

        PythonAgentBulkhead.Permit permit = pythonAgentBulkhead.tryAcquire(traceId);
        if (permit == null) {
            return busy(traceId);
        }

        try (permit) {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Trace-Id", traceId);
            HttpEntity<ChatRequest> httpEntity = new HttpEntity<>(request, headers);

            String url = agentBaseUrl + "/agent/chat";
            log.info("[{}] 调用 Python: {}", traceId, url);
            ResponseEntity<ChatResponse> response = restTemplate.postForEntity(
                    url, httpEntity, ChatResponse.class);

            log.info("[{}] Python 响应成功", traceId);
            return ResponseEntity.ok(response.getBody());
        } catch (HttpClientErrorException e) {
            if (e.getStatusCode().value() == HttpStatus.TOO_MANY_REQUESTS.value()) {
                log.warn("[{}] Python 并发已满", traceId);
                return busy(traceId);
            }
            log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
                    traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
            return ResponseEntity.ok(new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false
            ));
        } catch (Exception e) {
            log.error("[{}] 调用 Python 发生未知异常", traceId, e);
            return ResponseEntity.ok(new ChatResponse(
                    "当前 AI 服务暂时不可用，请稍后重试。",
                    "unknown",
                    traceId,
                    false
            ));
        }
    }

    private ResponseEntity<ChatResponse> busy(String traceId) {
        ChatResponse response = new ChatResponse(
                "当前请求较多，请稍后重试。",
                "unknown",
                traceId,
                false
        );
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, "1")
                .body(response);
    }
}
