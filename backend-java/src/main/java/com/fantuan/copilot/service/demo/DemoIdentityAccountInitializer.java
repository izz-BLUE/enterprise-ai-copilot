package com.fantuan.copilot.service.demo;

import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;

@Component
public class DemoIdentityAccountInitializer implements ApplicationRunner {
    private final DemoIdentityService identities;
    private final LeaveAccountRepository accounts;
    private final BusinessActionProperties actionProperties;
    private final Clock clock;

    public DemoIdentityAccountInitializer(DemoIdentityService identities,
                                          LeaveAccountRepository accounts,
                                          BusinessActionProperties actionProperties,
                                          Clock clock) {
        this.identities = identities;
        this.accounts = accounts;
        this.actionProperties = actionProperties;
        this.clock = clock;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        if (!identities.isEnabled()) {
            return;
        }
        for (DemoIdentity identity : identities.listEnabled()) {
            accounts.initialize(identity.employeeId(), identity.displayName(),
                    actionProperties.getDemoAnnualLeaveBalance(), clock.instant());
        }
    }
}
