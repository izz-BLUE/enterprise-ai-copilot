package com.fantuan.copilot.service.action.handler;

import com.fantuan.copilot.dto.action.AnnualLeaveActionProposal;
import com.fantuan.copilot.dto.action.AnnualLeaveSummary;
import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.leave.LeaveExecutionGateway;
import com.fantuan.copilot.gateway.leave.LeaveExecutionResult;
import com.fantuan.copilot.gateway.leave.LeaveSubmission;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.HalfDay;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.repository.action.LeaveAccountRepository;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.AnnualLeaveProposalValidator;
import com.fantuan.copilot.service.action.BusinessActionHandler;
import com.fantuan.copilot.service.demo.DemoIdentity;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;

/**
 * ANNUAL_LEAVE_REQUEST 业务动作 Handler（V2 §十七 AnnualLeaveActionHandler）。
 *
 * 承担原 BusinessActionService 内的业务专属逻辑：
 *  - planPending：proposal 复验 + 余额锁 + 冲突检查 + payloadJson 生成
 *  - revalidateBeforeExecute：confirm 时余额 / 冲突复检（返回 null=OK）
 *  - execute：LeaveExecutionGateway.submit + 余额扣减
 *  - buildSummary：AnnualLeaveSummary 渲染
 *
 * BusinessActionService 只保留通用生命周期（V2 §十七），不再持有
 * LeaveAccountRepository / LeaveExecutionGateway。
 */
@Component
public class AnnualLeaveActionHandler implements BusinessActionHandler {

    private static final String SUCCESS_MESSAGE = "模拟年假申请已提交。";
    private static final String TITLE = "提交模拟年假申请";

    private final LeaveAccountRepository accounts;
    private final LeaveExecutionGateway leaveExecutionGateway;

    public AnnualLeaveActionHandler(LeaveAccountRepository accounts,
                                    LeaveExecutionGateway leaveExecutionGateway) {
        this.accounts = accounts;
        this.leaveExecutionGateway = leaveExecutionGateway;
    }

    @Override
    public BusinessActionType supports() {
        return BusinessActionType.ANNUAL_LEAVE_REQUEST;
    }

    @Override
    public PendingPlan planPending(BusinessActionProposal proposal,
                                   DemoIdentity identity,
                                   LocalDate businessDate,
                                   Instant now) {
        if (!(proposal instanceof AnnualLeaveActionProposal annualLeave)) {
            throw new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "BUSINESS_RULE_VIOLATION", "年假申请参数不完整。", null, null);
        }
        AnnualLeaveProposalValidator.ValidatedLeave validated =
                AnnualLeaveProposalValidator.validate(annualLeave, businessDate);
        BigDecimal balanceBefore = accounts.findBalanceForUpdate(identity.employeeId())
                .orElseThrow(() -> new IllegalStateException("Demo leave account unavailable"));
        if (leaveExecutionGateway.hasConflict(identity.employeeId(),
                annualLeave.startDate(), annualLeave.endDate())) {
            throw rule("日期范围与已提交的模拟申请冲突。");
        }
        if (balanceBefore.compareTo(validated.days()) < 0) {
            throw rule("模拟年假余额不足。");
        }
        BigDecimal balanceAfter = balanceBefore.subtract(validated.days());
        String payloadJson = annualLeavePayloadJson(
                annualLeave.startDate(), annualLeave.endDate(), annualLeave.halfDay(),
                validated.reason(), validated.days(), balanceBefore, balanceAfter);
        return new PendingPlan(
                annualLeave.startDate(), annualLeave.endDate(), annualLeave.halfDay(),
                validated.reason(), validated.days(), balanceBefore, balanceAfter, payloadJson);
    }

    @Override
    public String revalidateBeforeExecute(PendingAction action) {
        BigDecimal currentBalance = accounts.findBalanceForUpdate(action.employeeId())
                .orElseThrow(() -> new IllegalStateException("Leave account unavailable"));
        if (currentBalance.compareTo(action.balanceBefore()) != 0
                || currentBalance.compareTo(action.days()) < 0
                || leaveExecutionGateway.hasConflict(action.employeeId(),
                action.startDate(), action.endDate())) {
            return "ACTION_STALE";
        }
        return null;
    }

    @Override
    public ExecutionExecutionResult execute(PendingAction action, Instant now) {
        LeaveExecutionResult execution = leaveExecutionGateway.submit(new LeaveSubmission(
                action.actionId(), action.employeeId(), action.startDate(), action.endDate(),
                action.halfDay(), action.days(), now));
        accounts.updateBalance(action.employeeId(),
                action.balanceBefore().subtract(action.days()), now);
        return new ExecutionExecutionResult(execution.requestId(), SUCCESS_MESSAGE);
    }

    @Override
    public PendingActionView buildSummary(PendingAction action, String plaintextNonce) {
        return new PendingActionView(
                action.actionId(), action.actionType(), action.status(), TITLE,
                new AnnualLeaveSummary(
                        action.displayName(), action.startDate(), action.endDate(),
                        action.halfDay(), action.days(), action.reason(),
                        action.balanceBefore(), action.balanceAfter()),
                plaintextNonce, action.expiresAt(),
                action.status() == com.fantuan.copilot.model.action.ActionStatus.PENDING_CONFIRMATION
                        && plaintextNonce != null);
    }

    private static ActionException rule(String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", message, null, null);
    }

    // 与 BusinessActionService.annualLeavePayloadJson 相同格式的辅助（保持单一来源：
    // phase 5 迁出后，Service 不再生成此 JSON；此处的 static helper 由 handler 持有）。
    static String annualLeavePayloadJson(LocalDate startDate, LocalDate endDate,
                                         HalfDay halfDay, String reason,
                                         BigDecimal days, BigDecimal balanceBefore,
                                         BigDecimal balanceAfter) {
        return "{\"startDate\":\"" + startDate + "\","
                + "\"endDate\":\"" + endDate + "\","
                + "\"halfDay\":\"" + halfDay.name() + "\","
                + "\"reason\":\"" + escapeJson(reason) + "\","
                + "\"days\":" + days.toPlainString() + ","
                + "\"balanceBefore\":" + balanceBefore.toPlainString() + ","
                + "\"balanceAfter\":" + balanceAfter.toPlainString() + ","
                + "\"schemaVersion\":1}";
    }

    static String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
