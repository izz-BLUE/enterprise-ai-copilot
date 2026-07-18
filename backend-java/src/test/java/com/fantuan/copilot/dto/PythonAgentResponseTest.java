package com.fantuan.copilot.dto;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fantuan.copilot.model.action.BusinessActionType;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class PythonAgentResponseTest {
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules();

    @Test
    void internalResponseDeserializesSnakeCaseProposal() throws Exception {
        String json = """
                {"answer":"draft","route":"action","safe":true,"category":"business_action",
                 "reason":"","sources":[],"success":true,"traceId":"origin",
                 "action_proposal":{"action_type":"ANNUAL_LEAVE_REQUEST","start_date":"2026-07-20",
                 "end_date":"2026-07-20","reason":"私事","half_day":"NONE"},"missing_fields":[]}
                """;
        PythonAgentResponse response = mapper.readValue(json, PythonAgentResponse.class);
        assertNotNull(response.actionProposal());
        assertEquals(BusinessActionType.ANNUAL_LEAVE_REQUEST, response.actionProposal().actionType());
    }

    @Test
    void publicNormalResponseOmitsPendingAction() throws Exception {
        AgentChatResponse response = new AgentChatResponse("answer", "rag", true,
                "normal", "", List.of(), true, "trace");
        String json = mapper.writeValueAsString(response);
        assertFalse(json.contains("pendingAction"));
    }
}
