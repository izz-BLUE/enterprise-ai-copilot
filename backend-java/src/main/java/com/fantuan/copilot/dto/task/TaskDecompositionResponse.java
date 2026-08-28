package com.fantuan.copilot.dto.task;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record TaskDecompositionResponse(
        String kind,
        List<TaskSpec> tasks,
        String reason) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TaskSpec(
            @JsonProperty("task_type") String taskType,
            @JsonProperty("task_text") String taskText,
            int sequence) {
    }
}
