package com.fantuan.copilot.service.demo;

import com.fantuan.copilot.service.action.ActionException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
public class DemoIdentityService {
    private static final List<DemoIdentity> IDENTITIES = List.of(
            new DemoIdentity("DEMO-001", "DEMO-001", "Demo User", DemoRole.EMPLOYEE),
            new DemoIdentity("DEMO-002", "DEMO-002", "Demo User B", DemoRole.EMPLOYEE),
            new DemoIdentity("DEMO-MGR-001", "DEMO-MGR-001", "Demo Manager", DemoRole.MANAGER));

    private final DemoIdentityProperties properties;
    private final Map<String, DemoIdentity> directory;

    public DemoIdentityService(DemoIdentityProperties properties) {
        this.properties = properties;
        Map<String, DemoIdentity> entries = new LinkedHashMap<>();
        for (DemoIdentity identity : IDENTITIES) {
            if (entries.put(identity.userId(), identity) != null) {
                throw new IllegalStateException("Duplicate demo identity");
            }
        }
        this.directory = Map.copyOf(entries);
    }

    public boolean isEnabled() {
        return properties.isEnabled();
    }

    public List<DemoIdentity> listEnabled() {
        requireEnabled();
        return IDENTITIES;
    }

    public DemoIdentity requireIdentity(String presentedUserId) {
        requireEnabled();
        if (presentedUserId == null || presentedUserId.trim().isEmpty()) {
            throw new ActionException(HttpStatus.BAD_REQUEST, "DEMO_IDENTITY_REQUIRED",
                    "请选择演示身份。", null, null);
        }
        return find(presentedUserId).orElseThrow(() -> new ActionException(
                HttpStatus.FORBIDDEN, "DEMO_IDENTITY_INVALID", "演示身份无效。", null, null));
    }

    public Optional<DemoIdentity> find(String presentedUserId) {
        if (presentedUserId == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(directory.get(presentedUserId.trim()));
    }

    private void requireEnabled() {
        if (!properties.isEnabled()) {
            throw new ActionException(HttpStatus.SERVICE_UNAVAILABLE, "DEMO_IDENTITY_DISABLED",
                    "演示身份功能当前未启用。", null, null);
        }
    }
}
