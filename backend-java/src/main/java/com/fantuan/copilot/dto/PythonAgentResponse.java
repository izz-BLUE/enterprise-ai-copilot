package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record PythonAgentResponse(
        String answer,
        String route,
        Boolean safe,
        String category,
        String reason,
        List<String> sources,
        Boolean success,
        @JsonAlias("trace_id") String traceId,
        @JsonAlias("action_proposal") AnnualLeaveActionProposal actionProposal,
        @JsonAlias("missing_fields") List<String> missingFields,
        @JsonAlias("memory_proposal") AgentMemoryProposal memoryProposal) {
}
