package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
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

    public HealthController(PythonAgentBulkhead pythonAgentBulkhead, JdbcTemplate jdbcTemplate) {
        this.pythonAgentBulkhead = pythonAgentBulkhead;
        this.jdbcTemplate = jdbcTemplate;
    }

    @GetMapping("/api/health")
    public ResponseEntity<Map<String, Object>> health() {
        try {
            jdbcTemplate.queryForObject("SELECT 1", Integer.class);
            return ResponseEntity.ok(Map.of(
                    "service", "backend-java", "status", "UP", "database", "UP",
                    "concurrency", pythonAgentBulkhead.snapshot()));
        } catch (RuntimeException exception) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(Map.of(
                    "service", "backend-java", "status", "DOWN", "database", "DOWN",
                    "concurrency", pythonAgentBulkhead.snapshot()));
        }
    }
}
