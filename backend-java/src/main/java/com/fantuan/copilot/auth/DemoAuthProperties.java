package com.fantuan.copilot.auth;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "demo.auth")
public class DemoAuthProperties {
    private boolean enabled;
    private String defaultPassword;
    private BigDecimal zhangsanAnnualBalance = new BigDecimal("10.0");
    private BigDecimal lisiAnnualBalance = new BigDecimal("5.0");
    private BigDecimal wangwuAnnualBalance = new BigDecimal("15.0");

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getDefaultPassword() {
        return defaultPassword;
    }

    public void setDefaultPassword(String defaultPassword) {
        this.defaultPassword = defaultPassword;
    }

    public BigDecimal getZhangsanAnnualBalance() {
        return zhangsanAnnualBalance;
    }

    public void setZhangsanAnnualBalance(BigDecimal zhangsanAnnualBalance) {
        this.zhangsanAnnualBalance = zhangsanAnnualBalance;
    }

    public BigDecimal getLisiAnnualBalance() {
        return lisiAnnualBalance;
    }

    public void setLisiAnnualBalance(BigDecimal lisiAnnualBalance) {
        this.lisiAnnualBalance = lisiAnnualBalance;
    }

    public BigDecimal getWangwuAnnualBalance() {
        return wangwuAnnualBalance;
    }

    public void setWangwuAnnualBalance(BigDecimal wangwuAnnualBalance) {
        this.wangwuAnnualBalance = wangwuAnnualBalance;
    }
}
