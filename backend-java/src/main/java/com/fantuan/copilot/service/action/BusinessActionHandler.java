package com.fantuan.copilot.service.action;

import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.service.demo.DemoIdentity;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

/**
 * 业务动作 Handler（V2 §十七）。
 *
 * BusinessActionService 只负责通用生命周期（feature enabled / admin /
 * identity / capacity / conversation 约束 / PendingAction 状态机 / nonce /
 * TTL / idempotency / audit / Memory 终态收口）；
 * 业务专属逻辑（校验 / 准备数据 / 执行副作用 / summary）由 Handler 完成。
 *
 * Registry：BusinessActionHandlerRegistry 按 BusinessActionType 分类；
 * BusinessActionService 通过 proposal.actionType() → handlerRegistry → handler
 * 调度，**禁止** instanceof / enum switch 分发（V2 §十七）。
 */
public interface BusinessActionHandler {

    /** 该 Handler 支持的 BusinessActionType（与 registry 键一致）。 */
    BusinessActionType supports();

    /**
     * createPending 阶段的业务校验（V2 §十七 validate）。
     * 生成 pending 所需的业务数据（如年假 days / balanceBefore / balanceAfter
     * / payloadJson 等）。校验失败抛 ActionException。
     * businessDate 来自 Java 侧可注入 Clock（业务日期），不是 handler 自行
     * LocalDate.now()。
     */
    PendingPlan planPending(BusinessActionProposal proposal,
                            DemoIdentity identity,
                            LocalDate businessDate,
                            Instant now);

    /**
     * confirm 阶段在 markProcessing 之后、状态机收口之前的业务前提复检
     * （V2 §十七 validate 的 confirm-time 版本）。返回 null 表示 OK；
     * 返回非 null 会触发 ACTION_STALE（由 Service 收口 FAILED + Memory ABANDONED）。
     */
    String revalidateBeforeExecute(PendingAction action);

    /**
     * confirm 阶段执行实际副作用（V2 §十七 execute）。
     * 返回执行结果（requestId / 成功消息），由 Service 写入 PendingAction 成功终态。
     */
    ExecutionExecutionResult execute(PendingAction action, Instant now);

    /**
     * 渲染 PendingAction 卡片 summary（V2 §十七 buildSummary）。
     * 供 Service 在 pendingView 里调用。
     */
    PendingActionView buildSummary(PendingAction action, String plaintextNonce);

    /**
     * createPending 业务准备结果。
     *
     * business 字段（startDate / endDate / halfDay / reason / days）为保留兼容
     * V1 语义与 V6 CHECK 的字段；EXPENSE_CLAIM 时全部为 null（V6 的
     * ck_business_action_leave_required 允许其为空），业务 payload 以
     * payloadJson 为 canonical（V2 §十八）。
     */
    record PendingPlan(
            java.time.LocalDate startDate,
            java.time.LocalDate endDate,
            com.fantuan.copilot.model.action.HalfDay halfDay,
            String reason,
            BigDecimal days,
            BigDecimal balanceBefore,
            BigDecimal balanceAfter,
            String payloadJson) {
    }

    /** execute 执行结果。 */
    record ExecutionExecutionResult(
            String requestId,
            String message) {
    }
}
