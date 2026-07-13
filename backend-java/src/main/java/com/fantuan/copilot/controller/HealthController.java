package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class HealthController {

    private final PythonAgentBulkhead pythonAgentBulkhead;

    public HealthController(PythonAgentBulkhead pythonAgentBulkhead) {
        this.pythonAgentBulkhead = pythonAgentBulkhead;
    }

    @GetMapping("/api/health")
    public Map<String, Object> health() {
        return Map.of(
                "service", "backend-java",
                "status", "UP",
                "concurrency", pythonAgentBulkhead.snapshot()
        );
    }
}
