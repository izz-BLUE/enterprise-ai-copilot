package com.fantuan.copilot.auth;

import com.fantuan.copilot.repository.action.LeaveAccount;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.math.BigDecimal;
import java.util.List;
import java.util.Objects;

@Component
public class DemoAuthAccountInitializer implements ApplicationRunner {
    private final DemoAuthProperties properties;
    private final AppUserRepository users;
    private final LeaveAccountRepository accounts;
    private final PasswordEncoder passwordEncoder;
    private final Clock clock;

    public DemoAuthAccountInitializer(DemoAuthProperties properties,
                                      AppUserRepository users,
                                      LeaveAccountRepository accounts,
                                      PasswordEncoder passwordEncoder,
                                      Clock clock) {
        this.properties = properties;
        this.users = users;
        this.accounts = accounts;
        this.passwordEncoder = passwordEncoder;
        this.clock = clock;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        if (!properties.isEnabled()) {
            return;
        }
        if (properties.getDefaultPassword() == null
                || properties.getDefaultPassword().isBlank()) {
            throw new IllegalStateException(
                    "DEMO_AUTH_DEFAULT_PASSWORD must be configured for legacy demo accounts when DEMO_AUTH_ENABLED=true");
        }
        requirePassword(properties.getPublicPassword(), "DEMO_PUBLIC_PASSWORD");
        requirePassword(properties.getInterviewPassword(), "DEMO_INTERVIEW_PASSWORD");
        requirePassword(properties.getAdminPassword(), "DEMO_ADMIN_PASSWORD");
        if (Objects.equals(properties.getPublicPassword(), properties.getInterviewPassword())
                || Objects.equals(properties.getPublicPassword(), properties.getAdminPassword())
                || Objects.equals(properties.getInterviewPassword(), properties.getAdminPassword())) {
            throw new IllegalStateException(
                    "DEMO_PUBLIC_PASSWORD, DEMO_INTERVIEW_PASSWORD and DEMO_ADMIN_PASSWORD must be different");
        }

        Instant now = clock.instant();
        List<SeedAccount> employees = List.of(
                new SeedAccount(DemoAuthPolicy.PUBLIC_DEMO_USER_ID,
                        DemoAuthPolicy.PUBLIC_DEMO_USERNAME,
                        DemoAuthPolicy.PUBLIC_DEMO_EMPLOYEE_ID, "公开演示账号",
                        AuthRole.EMPLOYEE, BigDecimal.ZERO),
                new SeedAccount("U10001", "zhangsan", "E10001", "张三",
                        AuthRole.EMPLOYEE, properties.getZhangsanAnnualBalance()),
                new SeedAccount("U10002", "lisi", "E10002", "李四",
                        AuthRole.EMPLOYEE, properties.getLisiAnnualBalance()),
                new SeedAccount("U10003", "wangwu", "E10003", "王五",
                        AuthRole.EMPLOYEE, properties.getWangwuAnnualBalance()));

        for (SeedAccount employee : employees) {
            ensureLeaveAccount(employee, now);
        }
        ensureAppUser(new SeedAccount("U90001", "admin", null, "管理员", AuthRole.ADMIN, null), now);
        for (SeedAccount employee : employees) {
            ensureAppUser(employee, now);
        }
    }

    private void requirePassword(String value, String environmentVariable) {
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(environmentVariable
                    + " must be configured when DEMO_AUTH_ENABLED=true");
        }
    }

    private void ensureLeaveAccount(SeedAccount seed, Instant now) {
        accounts.initialize(seed.employeeId(), seed.displayName(), seed.balance(), now);
        LeaveAccount account = accounts.findAccount(seed.employeeId()).orElseThrow(() ->
                new IllegalStateException("Demo leave account initialization failed: "
                        + seed.employeeId()));
        if (!seed.employeeId().equals(account.employeeId())
                || !seed.displayName().equals(account.displayName())) {
            throw new IllegalStateException("Existing leave account does not match demo auth seed: "
                    + seed.employeeId());
        }
    }

    private void ensureAppUser(SeedAccount seed, Instant now) {
        AppUser byId = users.findByUserId(seed.userId()).orElse(null);
        AppUser byUsername = users.findByUsername(seed.username()).orElse(null);
        if (byId != null && byUsername != null && !byId.userId().equals(byUsername.userId())) {
            throw new IllegalStateException("Demo auth seed has conflicting user identity: "
                    + seed.username());
        }
        AppUser existing = byId != null ? byId : byUsername;
        String password = passwordFor(seed);
        if (existing == null) {
            users.insert(new AppUser(seed.userId(), seed.username(),
                    passwordEncoder.encode(password), seed.employeeId(),
                    seed.displayName(), seed.role(), true, now));
            return;
        }
        if (!seed.userId().equals(existing.userId())
                || !seed.username().equals(existing.username())
                || !java.util.Objects.equals(seed.employeeId(), existing.employeeId())
                || !seed.displayName().equals(existing.displayName())
                || seed.role() != existing.role()) {
            throw new IllegalStateException("Existing app_user does not match demo auth seed: "
                    + seed.username());
        }
        // 保留运维人员选择的凭据。仅迁移旧的共享 seed password，避免配置独立
        // 凭据边界后，已有部署仍能接受 zhangsan/admin 使用旧密码。
        if (!passwordEncoder.matches(password, existing.passwordHash())
                && passwordEncoder.matches(properties.getDefaultPassword(), existing.passwordHash())) {
            users.updatePasswordHash(existing.userId(), passwordEncoder.encode(password));
        }
        // 有意保留已有的 enabled 标记和业务数据。
    }

    private String passwordFor(SeedAccount seed) {
        return switch (seed.username()) {
            case DemoAuthPolicy.PUBLIC_DEMO_USERNAME -> properties.getPublicPassword();
            case "zhangsan" -> properties.getInterviewPassword();
            case "admin" -> properties.getAdminPassword();
            default -> properties.getDefaultPassword();
        };
    }

    private record SeedAccount(String userId, String username, String employeeId,
                                String displayName, AuthRole role,
                                java.math.BigDecimal balance) {
    }
}
