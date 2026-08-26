package com.fantuan.copilot.dto.action;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;

import java.time.LocalDate;

/**
 * 年假业务动作 Proposal。
 *
 * V2 §十六：实现 BusinessActionProposal 接口，由 Jackson @JsonTypeInfo 按
 * "action_type" discriminator 反序列化。
 */
public record AnnualLeaveActionProposal(
        @JsonAlias("action_type") BusinessActionType actionType,
        @JsonAlias("start_date") LocalDate startDate,
        @JsonAlias("end_date") LocalDate endDate,
        String reason,
        @JsonAlias("half_day") HalfDay halfDay) implements BusinessActionProposal {

    // 注意：actionType() 直接返回 record 字段原值。untrusted proposal 的
    // action_type=null 会原样保留，由 AnnualLeaveProposalValidator 显式拒绝；
    // 不能在这里偷偷强制替换，否则会绕过"untrusted 输入必须复验"边界。
    // 该 record 语义上只能是 ANNUAL_LEAVE_REQUEST；Jackson 通过
    // @JsonSubTypes(name="ANNUAL_LEAVE_REQUEST") 与 defaultImpl 保证匹配。
}
