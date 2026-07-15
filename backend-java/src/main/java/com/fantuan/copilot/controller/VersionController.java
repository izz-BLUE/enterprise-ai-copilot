package com.fantuan.copilot.controller;

import com.fantuan.copilot.dto.VersionResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class VersionController {

    private final VersionResponse versionResponse;

    public VersionController(
            @Value("${app.version}") String version,
            @Value("${app.git-commit}") String gitCommit,
            @Value("${app.build-time}") String buildTime
    ) {
        this.versionResponse = new VersionResponse("backend-java", version, gitCommit, buildTime);
    }

    @GetMapping("/api/version")
    public VersionResponse version() {
        return versionResponse;
    }
}
