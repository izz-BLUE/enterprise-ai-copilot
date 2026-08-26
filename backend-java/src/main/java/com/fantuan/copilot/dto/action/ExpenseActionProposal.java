package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fantuan.copilot.model.action.BusinessActionType;

import java.util.List;

/**
 * 报销业务动作 Proposal（V2 §九、§十五）。
 *
 * 由 Python expense_proposal_tool 生成；Java ExpenseClaimActionHandler
 * 负责业务校验 / 准备数据 / 执行副作用 / buildSummary（Phase 6 实现）。
 *
 * 禁止携带 trusted identity 字段（V2 §十五）：
 *   employee_id / user_id / role / permission / token / nonce / idempotency_key
 *   均不进入 Proposal；Java 侧从 X-Employee-Id 注入。
 *
 * 业务字段含义：
 *   tripId              - 关联出差记录 ID（来源：travel_record_tool observation）
 *   expenseItems        - 费用明细列表（每项含 category / amount / invoiceId /
 *                         description）；不允许为空
 *   claimedAmount       - 申报金额（用户提出报销总额）
 *   reimbursableAmount  - 实报金额（deterministic 计算后填入）
 *   costCenter          - 成本中心（业务内部派生，V2 §十四 禁止作为 Tool 暴露）
 *   reason              - 报销原因 / 描述
 *   invoiceIds          - 涉及的发票 ID 列表（必须全部经过 invoice_verify_tool
 *                         验真成功）
 */
public record ExpenseActionProposal(
        @JsonAlias("action_type") BusinessActionType actionType,
        @JsonAlias("trip_id") String tripId,
        @JsonAlias("expense_items") List<ExpenseItemPayload> expenseItems,
        @JsonAlias("claimed_amount") java.math.BigDecimal claimedAmount,
        @JsonAlias("reimbursable_amount") java.math.BigDecimal reimbursableAmount,
        @JsonAlias("cost_center") String costCenter,
        String reason,
        @JsonAlias("invoice_ids") List<String> invoiceIds,
        @JsonAlias("stay_nights") Integer stayNights
) implements BusinessActionProposal {
    /**
     * 注意：actionType() 由 record accessor 自动生成并返回字段原值。
     * untrusted proposal 的 action_type=null 会原样保留，由
     * ExpenseClaimActionHandler 的 validator 显式拒绝，不能偷偷替换。
     *
     * Proposal 内嵌的费用项 payload。Phase 6 才被 ExpenseClaimActionHandler
     * 解析用于创建 ExpenseItem 行；本 Phase 仅作为 schema 占位。
     */
    public record ExpenseItemPayload(
            String category,
            java.math.BigDecimal amount,
            @JsonAlias("invoice_id") String invoiceId,
            String description) {
    }
}
