package com.fantuan.copilot.service.action.handler;

import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.ExpenseClaimSummary;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.expense.ExpenseExecutionGateway;
import com.fantuan.copilot.gateway.expense.ExpenseExecutionResult;
import com.fantuan.copilot.gateway.expense.ExpenseSubmission;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.ExpenseItem;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionHandler;
import com.fantuan.copilot.service.action.ExpenseActionPayload;
import com.fantuan.copilot.service.action.ExpenseActionPayloadCodec;
import com.fantuan.copilot.service.action.ExpenseCalculationService;
import com.fantuan.copilot.service.action.ExpensePrecheckService;
import com.fantuan.copilot.identity.VerifiedIdentity;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Set;

/**
 * EXPENSE_CLAIM 业务动作 Handler（V2 §十七 ExpenseClaimActionHandler）。
 *
 * 业务专属逻辑：
 *  - planPending：proposal 复验 + ExpensePrecheckService（items 完整/发票一致）
 *    + ExpenseCalculationService（酒店 750/晚封顶，其它合法实报）→ payloadJson
 *  - revalidateBeforeExecute：无本地余额类复检；confirm-time OA 重验证由
 *    BusinessActionHitlCoordinator 在数据库事务外完成
 *  - execute：ExpenseExecutionGateway.submit → 创建 ExpenseClaim + ExpenseItem
 *  - buildSummary：从 action_payload_json 反序列化 ExpenseClaimSummary
 *
 * handler 不调用 MCP / Java 只读端点 / RAG；当前业务事实由
 * ExpenseConfirmRevalidationService 在确认前从 Enterprise OA 重新取得。
 */
@Component
public class ExpenseClaimActionHandler implements BusinessActionHandler {

    private static final String TITLE = "提交模拟差旅报销申请";
    private static final String SUCCESS_MESSAGE = "模拟报销申请已提交。";

    private final ExpenseExecutionGateway expenseGateway;
    private final ExpensePrecheckService precheck;
    private final ExpenseCalculationService calculation;
    private final ExpenseActionPayloadCodec payloadCodec;

    public ExpenseClaimActionHandler(ExpenseExecutionGateway expenseGateway,
                                     ExpensePrecheckService precheck,
                                     ExpenseCalculationService calculation,
                                     ExpenseActionPayloadCodec payloadCodec) {
        this.expenseGateway = expenseGateway;
        this.precheck = precheck;
        this.calculation = calculation;
        this.payloadCodec = payloadCodec;
    }

    @Override
    public BusinessActionType supports() {
        return BusinessActionType.EXPENSE_CLAIM;
    }

    @Override
    public TaskType taskType() {
        return TaskType.EXPENSE_CLAIM;
    }

    @Override
    public TaskExecutionStatus statusAfterConfirmation() {
        return TaskExecutionStatus.WAITING_EXTERNAL;
    }

    @Override
    public Set<String> deterministicRegistrationRejectionCodes() {
        return Set.of("BUSINESS_RULE_VIOLATION", "EXPENSE_ITEMS_REQUIRED",
                "EXPENSE_AMOUNT_INVALID", "EXPENSE_INVOICES_REQUIRED");
    }

    @Override
    public Set<String> staleFailureCodes() {
        return Set.of("EXPENSE_TRIP_STALE", "EXPENSE_INVOICE_STALE", "EXPENSE_AMOUNT_STALE");
    }

    @Override
    public PendingPlan planPending(BusinessActionProposal proposal,
                                   VerifiedIdentity identity,
                                   LocalDate businessDate,
                                   Instant now) {
        if (!(proposal instanceof ExpenseActionProposal expense)) {
            throw actionRule("报销申请参数不完整。");
        }
        // 1. 结构校验（V2 §十五）
        if (expense.tripId() == null || expense.tripId().isBlank()) {
            throw actionRule("缺少出差记录(trip_id)。");
        }
        if (expense.reason() == null || expense.reason().isBlank()) {
            throw actionRule("缺少报销原因。");
        }
        if (expense.invoiceIds() == null || expense.invoiceIds().isEmpty()) {
            throw actionRule("缺少发票凭证。");
        }
        var payloadItems = expense.expenseItems();
        if (payloadItems == null || payloadItems.isEmpty()) {
            throw actionRule("报销明细不能为空。");
        }

        List<ExpenseItem> items = payloadItems.stream()
                .map(it -> new ExpenseItem(
                        it.invoiceId() == null ? "" : it.invoiceId(),
                        it.category(),
                        it.amount(),
                        it.description()))
                .toList();

        // 2. 确定性前置校验（V2 §十四：不暴露为 Planner Tool）
        var precheckResult = precheck.validate(items, expense.invoiceIds());
        if (!precheckResult.valid()) {
            throw new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                    precheckResult.errorCode(), precheckResult.message(), null, null);
        }

        // 3. 确定性金额计算（V2 §十一 / §十四：禁 LLM 计算）。
        // stayNights 由 Python expense_proposal_tool 从 travel_record 的
        // start_date/end_date 确定性计算后填入（缺省 1 晚兜底）。
        int stayNights = expense.stayNights() == null || expense.stayNights() < 1
                ? 1 : expense.stayNights();
        var calc = calculation.calculate(items, stayNights);
        if (expense.claimedAmount() == null
                || expense.claimedAmount().compareTo(calc.claimedAmount()) != 0) {
            throw actionRule("申报金额与费用明细合计不一致。");
        }
        if (expense.reimbursableAmount() != null
                && expense.reimbursableAmount().compareTo(calc.reimbursableAmount()) != 0) {
            throw actionRule("实报金额与系统计算不一致。");
        }

        String costCenter = expense.costCenter() == null || expense.costCenter().isBlank()
                ? "COST-DEFAULT" : expense.costCenter();
        String payloadJson = buildExpensePayloadJson(
                expense.tripId(), items, expense.claimedAmount(), calc.reimbursableAmount(),
                costCenter, expense.reason(), expense.invoiceIds());

        // V6 ck_business_action_leave_required: EXPENSE_CLAIM 时 leave 字段
        // （startDate/endDate/halfDay/reason/days/balanceBefore/balanceAfter）必须
        // 全 NULL；业务数据只走 action_payload_json（V2 §十八）。
        return new PendingPlan(null, null, null, null,
                null, null, null, payloadJson);
    }

    @Override
    public String revalidateBeforeExecute(PendingAction action) {
        // 外部 OA 重新校验必须留在 BusinessActionService 事务之外；coordinator
        // 会在 confirm() 前执行。
        return null;
    }

    @Override
    public ExecutionExecutionResult execute(PendingAction action, Instant now) {
        ExpenseActionPayload payload = payloadCodec.decode(action.actionPayloadJson());
        ExpenseExecutionResult execution = expenseGateway.submit(new ExpenseSubmission(
                action.actionId(), action.employeeId(),
                payload.tripId(), payload.costCenter(),
                payload.claimedAmount(), payload.reimbursableAmount(),
                payload.items(), now));
        return new ExecutionExecutionResult(execution.expenseId(), SUCCESS_MESSAGE);
    }

    @Override
    public PendingActionView buildSummary(PendingAction action, String plaintextNonce) {
        ExpenseActionPayload payload = payloadCodec.decode(action.actionPayloadJson());
        return new PendingActionView(
                action.actionId(), action.actionType(), action.status(), TITLE,
                new ExpenseClaimSummary(
                        payload.tripId(), payload.claimedAmount(), payload.reimbursableAmount(),
                        payload.costCenter(), payload.reason(),
                        payload.items().size(), payload.invoiceIds()),
                plaintextNonce, action.expiresAt(),
                action.status() == com.fantuan.copilot.model.action.ActionStatus.PENDING_CONFIRMATION
                        && plaintextNonce != null);
    }

    // ------------------------------------------------------------------
    // 内部辅助（确定性，无外部调用）
    // ------------------------------------------------------------------

    private static ActionException actionRule(String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY,
                "BUSINESS_RULE_VIOLATION", message, null, null);
    }

    private String buildExpensePayloadJson(String tripId, List<ExpenseItem> items,
                                           BigDecimal claimedAmount, BigDecimal reimbursableAmount,
                                           String costCenter, String reason,
                                           List<String> invoiceIds) {
        return payloadCodec.encode(tripId, items, claimedAmount, reimbursableAmount,
                costCenter, reason, invoiceIds);
    }
}
