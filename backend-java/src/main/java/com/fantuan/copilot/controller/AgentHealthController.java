package com.fantuan.copilot.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

@RestController
public class AgentHealthController {

    private final RestTemplate restTemplate;

    @Value("${python.agent.base-url:http://localhost:8000}")
    private String pythonAgentBaseUrl;

    public AgentHealthController(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @GetMapping("/api/agent/health")
    public Map<String, Object> agentHealth() {
        String url = pythonAgentBaseUrl + "/agent/health";
        return restTemplate.getForObject(url, Map.class);
    }
}
