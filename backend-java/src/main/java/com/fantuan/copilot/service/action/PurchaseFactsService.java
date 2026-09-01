package com.fantuan.copilot.service.action;

import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.Locale;

/** P4-3 本地确定性采购事实；Java 复核时不信任 Python 自报结果。 */
@Component
public class PurchaseFactsService {
    private static final BigDecimal AVAILABLE_BUDGET = new BigDecimal("20000.00");
    private static final BigDecimal DEVICE_LIMIT = new BigDecimal("20000.00");

    public BigDecimal availableBudget(String employeeId) {
        return "E10001".equals(employeeId) ? AVAILABLE_BUDGET : null;
    }

    public PolicyEvaluation evaluatePolicy(String itemName, BigDecimal requestedBudget,
                                           String justification) {
        if (justification == null || justification.isBlank()) {
            return new PolicyEvaluation(false, "PURCHASE_JUSTIFICATION_REQUIRED",
                    "采购申请必须提供 justification。");
        }
        if (requestedBudget == null || requestedBudget.signum() <= 0) {
            return new PolicyEvaluation(false, "PURCHASE_BUDGET_INVALID",
                    "requested_budget 必须大于 0。");
        }
        String normalized = itemName == null ? "" : itemName.toLowerCase(Locale.ROOT);
        if (!(normalized.contains("开发") || normalized.contains("mac")
                || normalized.contains("电脑") || normalized.contains("笔记本"))) {
            return new PolicyEvaluation(false, "PURCHASE_POLICY_DENIED",
                    "当前 fixture 只允许开发设备采购。");
        }
        if (requestedBudget.compareTo(DEVICE_LIMIT) > 0) {
            return new PolicyEvaluation(false, "PURCHASE_BUDGET_EXCEEDED",
                    "开发设备单次预算不得超过 20000。");
        }
        return new PolicyEvaluation(true, "", "开发设备单次预算不超过 20000 且 justification 已提供。");
    }

    public record PolicyEvaluation(boolean allowed, String code, String message) {
    }
}
