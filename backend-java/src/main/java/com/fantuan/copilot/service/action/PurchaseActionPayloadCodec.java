package com.fantuan.copilot.service.action;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

/** 采购 Proposal 与确认执行共用的 payload codec。 */
@Component
public class PurchaseActionPayloadCodec {
    private final ObjectMapper objectMapper;

    public PurchaseActionPayloadCodec(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public String encode(PurchaseActionPayload payload) {
        try {
            return objectMapper.writeValueAsString(payload);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new IllegalStateException("purchase payload 序列化失败", exception);
        }
    }

    public PurchaseActionPayload decode(String payloadJson) {
        if (payloadJson == null || payloadJson.isBlank()) {
            throw new IllegalStateException("purchase payload 为空");
        }
        try {
            return objectMapper.readValue(payloadJson, PurchaseActionPayload.class);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new IllegalStateException("purchase payload 解析失败", exception);
        }
    }
}
