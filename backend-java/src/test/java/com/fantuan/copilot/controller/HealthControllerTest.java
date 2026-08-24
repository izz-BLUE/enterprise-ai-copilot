package com.fantuan.copilot.controller;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.gateway.python.PythonAgentTransportException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class HealthControllerTest {

    @Test
    void livenessDoesNotProbeExternalDependencies() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        HealthController controller = new HealthController(new PythonAgentBulkhead(1, 10), jdbc, gateway);

        assertEquals(HttpStatus.OK, controller.health().getStatusCode());
        assertEquals("UP", controller.health().getBody().get("status"));
        verify(jdbc, never()).queryForObject("SELECT 1", Integer.class);
        verify(gateway, never()).readiness();
    }

    @Test
    void readinessRequiresDatabaseAndPython() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        PythonAgentGateway gateway = mock(PythonAgentGateway.class);
        when(jdbc.queryForObject("SELECT 1", Integer.class)).thenReturn(1);
        when(gateway.readiness()).thenReturn(Map.of("status", "READY"));
        HealthController controller = new HealthController(new PythonAgentBulkhead(1, 10), jdbc, gateway);

        assertEquals(HttpStatus.OK, controller.readiness().getStatusCode());

        when(gateway.readiness()).thenThrow(new PythonAgentTransportException(
                HttpStatus.SERVICE_UNAVAILABLE, "not ready", null));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, controller.readiness().getStatusCode());
    }
}
