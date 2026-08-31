package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
import com.fantuan.copilot.dto.memory.AgentMemoryProposal;

import java.util.List;

/**
 * Python → Java Agent 响应。
 *
 * V2 §十六：actionProposal 由 Jackson 按 action_type 自动分到具体 subtype
 * （AnnualLeaveActionProposal / ExpenseActionProposal），由 BusinessActionService
 * 按 proposal.actionType() → handlerRegistry 调度（不在 Controller 分发）。
 */
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
        @JsonAlias("action_proposal") BusinessActionProposal actionProposal,
        @JsonAlias("missing_fields") List<String> missingFields,
        @JsonAlias("memory_proposal") AgentMemoryProposal memoryProposal,
        @JsonAlias("hitl_wait") HitlWaitMarker hitlWait,
        @JsonAlias("external_wait") ExternalWaitMarker externalWait) {

    /** 兼容 P3-4 之前的测试 fixture 和 legacy 响应的构造方法。 */
    public PythonAgentResponse(String answer, String route, Boolean safe, String category,
                               String reason, List<String> sources, Boolean success,
                               String traceId, BusinessActionProposal actionProposal,
                               List<String> missingFields, AgentMemoryProposal memoryProposal) {
        this(answer, route, safe, category, reason, sources, success, traceId,
                actionProposal, missingFields, memoryProposal, null, null);
    }

    /** 兼容已包含 hitl_wait 的 P3-5A fixture 的构造方法。 */
    public PythonAgentResponse(String answer, String route, Boolean safe, String category,
                               String reason, List<String> sources, Boolean success,
                               String traceId, BusinessActionProposal actionProposal,
                               List<String> missingFields, AgentMemoryProposal memoryProposal,
                               HitlWaitMarker hitlWait) {
        this(answer, route, safe, category, reason, sources, success, traceId,
                actionProposal, missingFields, memoryProposal, hitlWait, null);
    }
}
