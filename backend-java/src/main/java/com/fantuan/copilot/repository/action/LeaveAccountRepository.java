package com.fantuan.copilot.repository.action;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;

public interface LeaveAccountRepository {
    void initialize(String employeeId, String displayName, BigDecimal balance, Instant now);
    Optional<LeaveAccount> findAccount(String employeeId);
    Optional<BigDecimal> findBalanceForUpdate(String employeeId);
    Optional<BigDecimal> findBalance(String employeeId);
    void updateBalance(String employeeId, BigDecimal balance, Instant now);
}
