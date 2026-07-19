package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fantuan.copilot.dto.action.PendingActionView;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record AgentChatResponse(
    String answer,
    String route,
    Boolean safe,
    String category,
    String reason,
    List<String> sources,
    Boolean success,
    String traceId,
    PendingActionView pendingAction
) {
    public AgentChatResponse(String answer, String route, Boolean safe, String category,
                             String reason, List<String> sources, Boolean success, String traceId) {
        this(answer, route, safe, category, reason, sources, success, traceId, null);
    }

    public static AgentChatResponse fromPython(PythonAgentResponse response,
                                               PendingActionView pendingAction) {
        return new AgentChatResponse(response.answer(), response.route(), response.safe(),
                response.category(), response.reason(), response.sources(), response.success(),
                response.traceId(), pendingAction);
    }
}
