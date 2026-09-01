package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import com.fantuan.copilot.model.action.BusinessActionType;

/**
 * 业务动作 Proposal 多态（V2 §十六）。
 *
 * Jackson 反序列化：根据 JSON 中的 "action_type" 字段（discriminator）自动
 * 分发到 AnnualLeaveActionProposal / ExpenseActionProposal 等具体 record。
 *
 * 注意：
 *   - 不允许携带 trusted identity 字段（employee_id / user_id / role / token /
 *     nonce / idempotency_key）；Java 侧从 X-Employee-Id 注入，不从 Proposal 读取。
 *   - 子类只承载业务专属字段；PendingAction 持久化边界以 action_payload_json
 *     JSONB 为 canonical（V2 §十八）。
 */
@JsonTypeInfo(
        use = JsonTypeInfo.Id.NAME,
        include = JsonTypeInfo.As.EXISTING_PROPERTY,
        property = "action_type",
        visible = true,
        defaultImpl = AnnualLeaveActionProposal.class)
@JsonSubTypes({
        @JsonSubTypes.Type(value = AnnualLeaveActionProposal.class,
                name = "ANNUAL_LEAVE_REQUEST"),
        @JsonSubTypes.Type(value = ExpenseActionProposal.class,
                name = "EXPENSE_CLAIM"),
        @JsonSubTypes.Type(value = PurchaseActionProposal.class,
                name = "PURCHASE_REQUEST")
})
public interface BusinessActionProposal {
    BusinessActionType actionType();
}
