package com.fantuan.copilot.controller;

import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class AgentHealthController {

    private final PythonAgentGateway pythonAgentGateway;

    public AgentHealthController(PythonAgentGateway pythonAgentGateway) {
        this.pythonAgentGateway = pythonAgentGateway;
    }

    @GetMapping("/api/agent/health")
    public ResponseEntity<Map<String, Object>> agentHealth() {
        try {
            return ResponseEntity.ok(pythonAgentGateway.health());
        } catch (PythonAgentTransportException exception) {
            return notReady();
        }
    }

    @GetMapping("/api/agent/ready")
    public ResponseEntity<Map<String, Object>> agentReadiness() {
        try {
            return ResponseEntity.ok(pythonAgentGateway.readiness());
        } catch (PythonAgentTransportException exception) {
            return notReady();
        }
    }

    private ResponseEntity<Map<String, Object>> notReady() {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(Map.of(
                "service", "agent-python",
                "status", "NOT_READY"));
    }
}
