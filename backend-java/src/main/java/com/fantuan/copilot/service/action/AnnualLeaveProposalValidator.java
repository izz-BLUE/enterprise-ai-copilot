package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import org.springframework.http.HttpStatus;

import java.math.BigDecimal;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;

/** 年假草稿的纯业务规则校验；不访问数据库，也不持有授权或执行职责。 */
public final class AnnualLeaveProposalValidator {
    private AnnualLeaveProposalValidator() {}

    public static ValidatedLeave validate(AnnualLeaveActionProposal proposal, LocalDate businessDate) {
        if (proposal == null || proposal.actionType() != BusinessActionType.ANNUAL_LEAVE_REQUEST
                || proposal.startDate() == null || proposal.endDate() == null
                || proposal.halfDay() == null) {
            throw rule("年假申请参数不完整。");
        }
        if (proposal.startDate().isBefore(businessDate)) {
            throw rule("开始日期不能早于当前业务日期。");
        }
        if (proposal.endDate().isBefore(proposal.startDate())) {
            throw rule("结束日期不能早于开始日期。");
        }
        long span = ChronoUnit.DAYS.between(proposal.startDate(), proposal.endDate()) + 1;
        if (span > 31) {
            throw rule("申请日期跨度不能超过31个日历日。");
        }
        String rawReason = proposal.reason() == null ? "" : proposal.reason();
        String reason = rawReason.trim();
        if (reason.isEmpty() || reason.length() > 200
                || rawReason.codePoints().anyMatch(Character::isISOControl)) {
            throw rule("申请原因必须为1到200个非控制字符。");
        }
        BigDecimal days;
        if (proposal.halfDay() == HalfDay.AM || proposal.halfDay() == HalfDay.PM) {
            if (!proposal.startDate().equals(proposal.endDate()) || isWeekend(proposal.startDate())) {
                throw rule("半天年假仅支持工作日单日申请。");
            }
            days = new BigDecimal("0.5");
        } else {
            long weekdays = 0;
            for (LocalDate day = proposal.startDate(); !day.isAfter(proposal.endDate()); day = day.plusDays(1)) {
                if (!isWeekend(day)) {
                    weekdays++;
                }
            }
            days = BigDecimal.valueOf(weekdays).setScale(1);
        }
        if (days.compareTo(BigDecimal.ZERO) <= 0) {
            throw rule("申请日期范围不包含有效工作日。");
        }
        return new ValidatedLeave(reason, days);
    }

    private static boolean isWeekend(LocalDate date) {
        return date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY;
    }

    private static ActionException rule(String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", message, null, null);
    }

    public record ValidatedLeave(String reason, BigDecimal days) {}
}
