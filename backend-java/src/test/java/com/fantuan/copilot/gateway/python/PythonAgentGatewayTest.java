package com.fantuan.copilot.gateway.python;

import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import java.net.SocketTimeoutException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PythonAgentGatewayTest {

    @Test
    void mapsUpstreamHttpAndTimeoutWithoutLeakingBodies() {
        RestTemplate rest = mock(RestTemplate.class);
        PythonAgentGateway gateway = new PythonAgentGateway(
                rest, new PythonAgentBulkhead(1, 10), "http://python");

        when(rest.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(HttpClientErrorException.create(
                        HttpStatus.BAD_REQUEST, "bad", HttpHeaders.EMPTY,
                        "secret-body".getBytes(), null));
        PythonAgentTransportException upstream = assertThrows(
                PythonAgentTransportException.class,
                () -> gateway.post("/agent/chat", new Object(), null,
                        String.class, "trace"));
        assertEquals(HttpStatus.BAD_GATEWAY, upstream.responseStatus());

        when(rest.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(new ResourceAccessException(
                        "timeout", new SocketTimeoutException("provider detail")));
        PythonAgentTransportException timeout = assertThrows(
                PythonAgentTransportException.class,
                () -> gateway.post("/agent/chat", new Object(), null,
                        String.class, "trace"));
        assertEquals(HttpStatus.GATEWAY_TIMEOUT, timeout.responseStatus());
    }

    @Test
    void mapsUpstream429ToBusyContract() {
        RestTemplate rest = mock(RestTemplate.class);
        PythonAgentGateway gateway = new PythonAgentGateway(
                rest, new PythonAgentBulkhead(1, 10), "http://python");
        when(rest.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(HttpClientErrorException.create(
                        HttpStatus.TOO_MANY_REQUESTS, "busy", HttpHeaders.EMPTY,
                        new byte[0], null));

        assertThrows(PythonAgentBusyException.class,
                () -> gateway.post("/agent/chat", new Object(), null,
                        String.class, "trace"));
    }

    @Test
    void mapsUpstream409ToConflictWithoutTurningItIntoBadGateway() {
        RestTemplate rest = mock(RestTemplate.class);
        PythonAgentGateway gateway = new PythonAgentGateway(
                rest, new PythonAgentBulkhead(1, 10), "http://python");
        when(rest.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(HttpClientErrorException.create(
                        HttpStatus.CONFLICT, "recovery conflict", HttpHeaders.EMPTY,
                        new byte[0], null));

        PythonAgentTransportException conflict = assertThrows(
                PythonAgentTransportException.class,
                () -> gateway.post("/agent/chat", new Object(), null,
                        String.class, "trace"));
        assertEquals(HttpStatus.CONFLICT, conflict.responseStatus());
    }

    @Test
    void mapsUpstream500ToBadGateway() {
        RestTemplate rest = mock(RestTemplate.class);
        PythonAgentGateway gateway = new PythonAgentGateway(
                rest, new PythonAgentBulkhead(1, 10), "http://python");
        when(rest.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(HttpClientErrorException.create(
                        HttpStatus.INTERNAL_SERVER_ERROR, "upstream error", HttpHeaders.EMPTY,
                        new byte[0], null));

        PythonAgentTransportException upstream = assertThrows(
                PythonAgentTransportException.class,
                () -> gateway.post("/agent/chat", new Object(), null,
                        String.class, "trace"));
        assertEquals(HttpStatus.BAD_GATEWAY, upstream.responseStatus());
    }
}
