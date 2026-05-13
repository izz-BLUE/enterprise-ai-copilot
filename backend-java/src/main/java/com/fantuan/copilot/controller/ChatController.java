package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

@RestController
public class ChatController {

    private final RestClient restClient;

    public ChatController(RestClient restClient) {
        this.restClient = restClient;
    }

    @PostMapping("/api/chat")
    public ChatResponse chat(@RequestBody ChatRequest request) {
        return restClient.post()
                .uri("http://localhost:8000/agent/chat")
                .body(request)
                .retrieve()
                .body(ChatResponse.class);
    }
}
