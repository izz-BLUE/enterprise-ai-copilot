package com.fantuan.copilot.service.action;

import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;

@Component
public class DemoLeaveAccountInitializer implements ApplicationRunner {
    public static final String EMPLOYEE_ID = "DEMO-001";
    public static final String DISPLAY_NAME = "Demo User";

    private final LeaveAccountRepository repository;
    private final BusinessActionProperties properties;
    private final Clock clock;

    public DemoLeaveAccountInitializer(LeaveAccountRepository repository,
                                       BusinessActionProperties properties,
                                       Clock clock) {
        this.repository = repository;
        this.properties = properties;
        this.clock = clock;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        repository.initialize(EMPLOYEE_ID, DISPLAY_NAME,
                properties.getDemoAnnualLeaveBalance(), clock.instant());
    }
}
