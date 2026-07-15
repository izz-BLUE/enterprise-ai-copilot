package com.fantuan.copilot.dto;

public record VersionResponse(
        String service,
        String version,
        String gitCommit,
        String buildTime
) {
}
