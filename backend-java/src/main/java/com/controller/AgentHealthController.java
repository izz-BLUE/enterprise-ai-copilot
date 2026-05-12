package com.fantuan.copilot.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

import java.util.Map;

@RestController
public class AgentHealthController {

    private final RestClient restClient;

    public AgentHealthController(RestClient restClient) {
        this.restClient = restClient;
    }

    @GetMapping("/api/agent/health")
    public Map<String, Object> agentHealth() {
        return restClient.get()
                .uri("http://localhost:8000/agent/health")
                .retrieve()
                .body(Map.class);
    }
}