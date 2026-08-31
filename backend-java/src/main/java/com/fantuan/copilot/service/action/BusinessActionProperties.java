package com.fantuan.copilot.service.action;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;
import org.springframework.validation.annotation.Validated;

import java.math.BigDecimal;
import java.time.ZoneId;

@Component
@Validated
@ConfigurationProperties(prefix = "business.actions")
public class BusinessActionProperties {
    private boolean enabled = false;
    private boolean requireAdmin = false;
    @Min(1)
    private long ttlSeconds = 600;
    @Min(1)
    private int maxPending = 100;
    @Min(1)
    private int maxCompleted = 500;
    @DecimalMin("0.0")
    private BigDecimal demoAnnualLeaveBalance = new BigDecimal("5.0");
    private String timezone = "Asia/Shanghai";

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public boolean isRequireAdmin() { return requireAdmin; }
    public void setRequireAdmin(boolean requireAdmin) { this.requireAdmin = requireAdmin; }
    public long getTtlSeconds() { return ttlSeconds; }
    public void setTtlSeconds(long ttlSeconds) { this.ttlSeconds = ttlSeconds; }
    public int getMaxPending() { return maxPending; }
    public void setMaxPending(int maxPending) { this.maxPending = maxPending; }
    public int getMaxCompleted() { return maxCompleted; }
    public void setMaxCompleted(int maxCompleted) { this.maxCompleted = maxCompleted; }
    public BigDecimal getDemoAnnualLeaveBalance() { return demoAnnualLeaveBalance; }
    public void setDemoAnnualLeaveBalance(BigDecimal balance) { this.demoAnnualLeaveBalance = balance; }
    public String getTimezone() { return timezone; }
    public void setTimezone(String timezone) {
        ZoneId.of(timezone);
        this.timezone = timezone;
    }
    public ZoneId zoneId() { return ZoneId.of(timezone); }
}
