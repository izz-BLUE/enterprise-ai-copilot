package com.fantuan.copilot.service.action;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.model.action.ExpenseItem;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.List;

/** Single JSON codec shared by expense proposal persistence and confirmation. */
@Component
public class ExpenseActionPayloadCodec {
    private final ObjectMapper objectMapper;

    public ExpenseActionPayloadCodec(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public String encode(String tripId, List<ExpenseItem> items,
                         BigDecimal claimedAmount, BigDecimal reimbursableAmount,
                         String costCenter, String reason, List<String> invoiceIds) {
        try {
            return objectMapper.writeValueAsString(new ExpenseActionPayload(
                    1, tripId, items, claimedAmount, reimbursableAmount,
                    costCenter, reason, invoiceIds));
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new IllegalStateException("expense payload 序列化失败", exception);
        }
    }

    public ExpenseActionPayload decode(String payloadJson) {
        if (payloadJson == null || payloadJson.isBlank()) {
            throw new IllegalStateException("expense payload 为空");
        }
        try {
            return objectMapper.readValue(payloadJson, ExpenseActionPayload.class);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new IllegalStateException("expense payload 解析失败", exception);
        }
    }
}
