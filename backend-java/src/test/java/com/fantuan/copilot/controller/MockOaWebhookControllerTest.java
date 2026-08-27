package com.fantuan.copilot.controller;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.dto.webhook.MockOaExpenseApprovalWebhook;
import com.fantuan.copilot.security.MockOaWebhookVerifier;
import com.fantuan.copilot.service.action.MockOaWebhookProcessingService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HexFormat;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class MockOaWebhookControllerTest {
    private static final String SECRET = "test-webhook-secret";
    private static final Instant NOW = Instant.parse("2026-08-27T10:00:00Z");
    private static final String BODY = "{\"eventId\":\"evt-1\",\"eventType\":\"EXPENSE_APPROVAL_CHANGED\","
            + "\"requestId\":\"OA-EXP-1\"}";

    @Mock MockOaWebhookProcessingService processingService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        MockOaWebhookVerifier verifier = new MockOaWebhookVerifier(
                SECRET, 300, Clock.fixed(NOW, ZoneOffset.UTC));
        ObjectMapper globallyLenientMapper = new ObjectMapper()
                .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
        mockMvc = MockMvcBuilders.standaloneSetup(new MockOaWebhookController(
                globallyLenientMapper, verifier, processingService)).build();
    }

    @Test
    void validHmacIsAccepted() throws Exception {
        mockMvc.perform(signedPost(BODY, NOW.getEpochSecond()))
                .andExpect(status().isNoContent());

        verify(processingService).process(any(MockOaExpenseApprovalWebhook.class));
    }

    @Test
    void wrongHmacIsRejected() throws Exception {
        mockMvc.perform(signedPost(BODY, NOW.getEpochSecond(), "v1=" + "0".repeat(64)))
                .andExpect(status().isUnauthorized());

        verify(processingService, never()).process(any());
    }

    @Test
    void modifiedBodyWithOldSignatureIsRejected() throws Exception {
        String signature = signature(BODY, NOW.getEpochSecond());

        mockMvc.perform(post(MockOaWebhookController.PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY.replace("OA-EXP-1", "OA-EXP-2"))
                        .header("X-Mock-OA-Timestamp", NOW.getEpochSecond())
                        .header("X-Mock-OA-Signature", signature))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void staleTimestampIsRejected() throws Exception {
        long stale = NOW.getEpochSecond() - 301;

        mockMvc.perform(signedPost(BODY, stale))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void malformedTimestampIsRejected() throws Exception {
        mockMvc.perform(post(MockOaWebhookController.PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY)
                        .header("X-Mock-OA-Timestamp", "not-an-epoch")
                        .header("X-Mock-OA-Signature", "v1=" + "0".repeat(64)))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void malformedBodyIsBadRequestAfterAuthentication() throws Exception {
        String body = "{not-json";

        mockMvc.perform(signedPost(body, NOW.getEpochSecond()))
                .andExpect(status().isBadRequest());

        verify(processingService, never()).process(any());
    }

    @Test
    void unknownStatusFieldIsRejectedEvenWhenGlobalMapperIgnoresUnknownProperties() throws Exception {
        String body = BODY.replace("}", ",\"status\":\"APPROVED\"}");

        mockMvc.perform(signedPost(body, NOW.getEpochSecond()))
                .andExpect(status().isBadRequest());

        verify(processingService, never()).process(any());
    }

    private MockHttpServletRequestBuilder signedPost(String body, long timestamp) throws Exception {
        return signedPost(body, timestamp, signature(body, timestamp));
    }

    private MockHttpServletRequestBuilder signedPost(String body, long timestamp, String signature) {
        return post(MockOaWebhookController.PATH)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body)
                .header("X-Mock-OA-Timestamp", timestamp)
                .header("X-Mock-OA-Signature", signature);
    }

    private String signature(String body, long timestamp) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        byte[] input = (timestamp + ".").getBytes(StandardCharsets.UTF_8);
        mac.update(input);
        byte[] digest = mac.doFinal(body.getBytes(StandardCharsets.UTF_8));
        return "v1=" + HexFormat.of().formatHex(digest);
    }
}
