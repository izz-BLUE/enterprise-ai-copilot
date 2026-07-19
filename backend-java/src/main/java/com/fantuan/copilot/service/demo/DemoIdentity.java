package com.fantuan.copilot.service.demo;

public record DemoIdentity(
        String userId,
        String employeeId,
        String displayName,
        DemoRole role) {
}
