package com.fantuan.copilot.controller;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fantuan.copilot.concurrency.PythonAgentBulkhead;
import com.fantuan.copilot.dto.ChatRequest;
import com.fantuan.copilot.dto.ChatResponse;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ChatControllerLoggingTest {

    @Test
    void requestAndUpstreamErrorLogsExposeOnlySafeMetadata() {
        String questionMarker = "secret-like-marker-user-question";
        String upstreamMarker = "upstream-secret-marker";
        String traceId = "trace-log-minimization";

        RestTemplate restTemplate = mock(RestTemplate.class);
        HttpClientErrorException upstreamError = HttpClientErrorException.create(
                HttpStatus.BAD_REQUEST,
                "Bad Request",
                HttpHeaders.EMPTY,
                upstreamMarker.getBytes(StandardCharsets.UTF_8),
                StandardCharsets.UTF_8);
        when(restTemplate.postForEntity(anyString(), any(), eq(ChatResponse.class)))
                .thenThrow(upstreamError);

        ChatController controller = new ChatController(new PythonAgentGateway(
                restTemplate, new PythonAgentBulkhead(1, 10), "http://python-agent:8000"));
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getAttribute("traceId")).thenReturn(traceId);

        Logger logger = (Logger) LoggerFactory.getLogger(ChatController.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            controller.chat(new ChatRequest(questionMarker), request);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        List<String> messages = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .toList();
        String joined = String.join("\n", messages);
        assertFalse(joined.contains(questionMarker));
        assertFalse(joined.contains(upstreamMarker));
        assertTrue(joined.contains(traceId));
        assertTrue(joined.contains("messageLength=" + questionMarker.length()));
        assertTrue(joined.contains("502 BAD_GATEWAY"));
    }
}
