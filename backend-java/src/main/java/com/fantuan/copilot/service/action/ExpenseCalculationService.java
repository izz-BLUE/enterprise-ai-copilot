package com.fantuan.copilot.service.action;

import com.fantuan.copilot.model.action.ExpenseItem;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;

/**
 * 报销金额确定性计算（V2 §十一 / §十四）。
 *
 * 不是 Planner Tool；只在 ExpenseClaimActionHandler 内部调用。
 *
 * V1 固定 demo 规则（与 RAG 政策知识相互独立 —— RAG 只提供知识/解释上下文，
 * 金额与限额判断的最终权威规则写在确定性业务代码里，禁止解析 RAG 自然语言）：
 * - HOTEL（酒店）：每晚最高 750，按天数 × 750 封顶；超过部分不计入实报。
 * - TAXI（市内交通）：合法发票实报。
 * - TRAIN / FLIGHT（高铁/机票）：合法凭证实报。
 * - MEAL（餐饮）：合法发票实报；demo fixture 不做单独限额。
 *
 * claimedAmount 为用户申报总额（所有 items 的 amount 之和）；
 * reimbursableAmount 为按上规则计算后的实报总额；
 * 所有舍入到 0.01（四舍五入）。
 */
@Component
public class ExpenseCalculationService {

    public static final BigDecimal HOTEL_NIGHTLY_CAP = new BigDecimal("750");

    public record CalculationResult(
            BigDecimal claimedAmount,
            BigDecimal reimbursableAmount) {
    }

    public CalculationResult calculate(List<ExpenseItem> items, int stayNights) {
        BigDecimal claimed = BigDecimal.ZERO;
        BigDecimal reimbursable = BigDecimal.ZERO;
        for (ExpenseItem item : items) {
            BigDecimal amount = item.amount();
            claimed = claimed.add(amount);
            if ("HOTEL".equals(item.category())) {
                BigDecimal cap = HOTEL_NIGHTLY_CAP.multiply(
                        BigDecimal.valueOf(Math.max(stayNights, 1)));
                reimbursable = reimbursable.add(amount.min(cap));
            } else {
                // TAXI / TRAIN / FLIGHT / MEAL：合法发票实报（V1 demo 规则）
                reimbursable = reimbursable.add(amount);
            }
        }
        return new CalculationResult(
                claimed.setScale(2, RoundingMode.HALF_UP),
                reimbursable.setScale(2, RoundingMode.HALF_UP));
    }
}
