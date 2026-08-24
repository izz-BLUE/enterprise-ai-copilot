package com.fantuan.copilot.dto;

import java.util.List;

public record ChatResponse(String answer, String model, String traceId, boolean success,
                           List<String> sources) {
}
