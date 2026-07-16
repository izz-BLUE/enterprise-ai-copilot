package com.fantuan.copilot.config;

import com.fantuan.copilot.service.action.BusinessActionProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;

@Configuration
public class BusinessActionConfig {
    @Bean
    Clock businessClock(BusinessActionProperties properties) {
        return Clock.system(properties.zoneId());
    }
}
