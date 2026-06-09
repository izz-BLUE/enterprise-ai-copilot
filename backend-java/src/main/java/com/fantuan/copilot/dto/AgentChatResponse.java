package com.fantuan.copilot.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record AgentChatResponse(
    String answer,
    String route,
    Boolean safe,
    String category,
    String reason,
    List<String> sources,
    Boolean success,
    String traceId
) {}
