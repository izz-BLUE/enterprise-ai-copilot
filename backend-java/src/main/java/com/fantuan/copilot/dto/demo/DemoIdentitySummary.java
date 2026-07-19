package com.fantuan.copilot.dto.demo;

import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoRole;

public record DemoIdentitySummary(String userId, String displayName, DemoRole role) {
    public static DemoIdentitySummary from(DemoIdentity identity) {
        return new DemoIdentitySummary(identity.userId(), identity.displayName(), identity.role());
    }
}
