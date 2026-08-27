package com.fantuan.copilot.controller;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectReader;
import com.fantuan.copilot.dto.webhook.MockOaExpenseApprovalWebhook;
import com.fantuan.copilot.security.MockOaWebhookAuthenticationException;
import com.fantuan.copilot.security.MockOaWebhookVerifier;
import com.fantuan.copilot.service.action.MockOaWebhookPayloadException;
import com.fantuan.copilot.service.action.MockOaWebhookProcessingException;
import com.fantuan.copilot.service.action.MockOaWebhookProcessingService;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;

/** Authenticated Mock OA notification endpoint; the notification body is never status authority. */
@RestController
public class MockOaWebhookController {
    public static final String PATH = "/api/webhooks/mock-oa/expense-approval";

    private static final Logger log = LoggerFactory.getLogger(MockOaWebhookController.class);

    private final ObjectReader strictWebhookReader;
    private final MockOaWebhookVerifier verifier;
    private final MockOaWebhookProcessingService processingService;

    public MockOaWebhookController(ObjectMapper objectMapper,
                                   MockOaWebhookVerifier verifier,
                                   MockOaWebhookProcessingService processingService) {
        this.strictWebhookReader = objectMapper.readerFor(MockOaExpenseApprovalWebhook.class)
                .with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
        this.verifier = verifier;
        this.processingService = processingService;
    }

    @PostMapping(PATH)
    public ResponseEntity<Void> receive(
            @RequestHeader(value = "X-Mock-OA-Timestamp", required = false) String timestamp,
            @RequestHeader(value = "X-Mock-OA-Signature", required = false) String signature,
            @RequestBody(required = false) byte[] rawBody,
            HttpServletRequest request) {
        byte[] body = rawBody == null ? new byte[0] : rawBody;
        verifier.verify(body, timestamp, signature);
        MockOaExpenseApprovalWebhook webhook = parse(body);
        log.info("[{}] accepted Mock OA approval notification eventId={} requestIdPrefix={}",
                traceId(request), webhook.eventId(), requestIdPrefix(webhook.requestId()));
        processingService.process(webhook);
        return ResponseEntity.noContent().build();
    }

    @ExceptionHandler(MockOaWebhookAuthenticationException.class)
    ResponseEntity<Void> handleAuthentication(MockOaWebhookAuthenticationException exception) {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
    }

    @ExceptionHandler(MockOaWebhookPayloadException.class)
    ResponseEntity<Void> handlePayload(MockOaWebhookPayloadException exception) {
        return ResponseEntity.badRequest().build();
    }

    @ExceptionHandler(MockOaWebhookProcessingException.class)
    ResponseEntity<Void> handleProcessing(MockOaWebhookProcessingException exception) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).build();
    }

    private MockOaExpenseApprovalWebhook parse(byte[] rawBody) {
        try {
            MockOaExpenseApprovalWebhook webhook = strictWebhookReader.readValue(rawBody);
            webhook.validate();
            return webhook;
        } catch (IOException | IllegalArgumentException exception) {
            throw new MockOaWebhookPayloadException("invalid Mock OA webhook body", exception);
        }
    }

    private String traceId(HttpServletRequest request) {
        Object value = request.getAttribute("traceId");
        return value == null ? "unknown" : value.toString();
    }

    private String requestIdPrefix(String requestId) {
        return requestId.substring(0, Math.min(requestId.length(), 12));
    }
}
