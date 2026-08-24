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
                 "end_date":"2026-07-20","reason":"私事","half_day":"NONE"},"missing_fields":[],
                 "memory_proposal":{"task_type":"LEAVE_REQUEST",
                 "task_state":{"waiting_for":"confirmation"},"summary":"等待确认"}}
                """;
        PythonAgentResponse response = mapper.readValue(json, PythonAgentResponse.class);
        assertNotNull(response.actionProposal());
        assertEquals(BusinessActionType.ANNUAL_LEAVE_REQUEST, response.actionProposal().actionType());
        assertNotNull(response.memoryProposal());
        assertEquals("LEAVE_REQUEST", response.memoryProposal().taskType());
        assertEquals("confirmation", response.memoryProposal().taskState().get("waiting_for"));
    }

    @Test
    void memoryProposalRejectsOwnerOrLifecycleFields() {
        String json = """
                {"answer":"ok","route":"rag","safe":true,"category":"normal",
                 "reason":"","sources":[],"success":true,
                 "memory_proposal":{"task_type":"GENERIC","task_state":{},"summary":"ok",
                 "user_id":"forged","status":"COMPLETED"}}
                """;
        assertThrows(com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException.class,
                () -> mapper.readValue(json, PythonAgentResponse.class));
    }

    @Test
    void publicNormalResponseOmitsPendingAction() throws Exception {
        AgentChatResponse response = new AgentChatResponse("answer", "rag", true,
                "normal", "", List.of(), true, "trace");
        String json = mapper.writeValueAsString(response);
        assertFalse(json.contains("pendingAction"));
    }
}
