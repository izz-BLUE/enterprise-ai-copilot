package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class HealthController {

    private final PythonAgentBulkhead pythonAgentBulkhead;
    private final JdbcTemplate jdbcTemplate;
    private final PythonAgentGateway pythonAgentGateway;

    public HealthController(PythonAgentBulkhead pythonAgentBulkhead,
                            JdbcTemplate jdbcTemplate,
                            PythonAgentGateway pythonAgentGateway) {
        this.pythonAgentBulkhead = pythonAgentBulkhead;
        this.jdbcTemplate = jdbcTemplate;
        this.pythonAgentGateway = pythonAgentGateway;
    }

    @GetMapping("/api/health")
    public ResponseEntity<Map<String, Object>> health() {
        return ResponseEntity.ok(Map.of(
                "service", "backend-java",
                "status", "UP",
                "concurrency", pythonAgentBulkhead.snapshot()));
    }

    @GetMapping("/api/ready")
    public ResponseEntity<Map<String, Object>> readiness() {
        try {
            jdbcTemplate.queryForObject("SELECT 1", Integer.class);
            pythonAgentGateway.readiness();
            return ResponseEntity.ok(Map.of(
                    "service", "backend-java",
                    "status", "READY",
                    "database", "UP",
                    "agent", "READY",
                    "concurrency", pythonAgentBulkhead.snapshot()));
        } catch (RuntimeException exception) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(Map.of(
                    "service", "backend-java",
                    "status", "NOT_READY",
                    "concurrency", pythonAgentBulkhead.snapshot()));
        }
    }
}
