package com.fantuan.copilot.service.action.handler;

import com.fantuan.copilot.dto.action.BusinessActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.action.PurchaseActionProposal;
import com.fantuan.copilot.dto.action.PurchaseSummary;
import com.fantuan.copilot.gateway.purchase.PurchaseExecutionGateway;
import com.fantuan.copilot.gateway.purchase.PurchaseExecutionResult;
import com.fantuan.copilot.gateway.purchase.PurchaseSubmission;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PendingAction;
import com.fantuan.copilot.model.task.TaskExecutionStatus;
import com.fantuan.copilot.model.task.TaskType;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.service.action.ActionException;
import com.fantuan.copilot.service.action.BusinessActionHandler;
import com.fantuan.copilot.service.action.PurchaseActionPayload;
import com.fantuan.copilot.service.action.PurchaseActionPayloadCodec;
import com.fantuan.copilot.service.action.PurchaseFactsService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Set;

/** P4-3 Purchase domain proof: validate, persist a pending proposal, then submit. */
@Component
public class PurchaseRequestActionHandler implements BusinessActionHandler {
    private static final String TITLE = "提交模拟采购申请";
    private static final String SUCCESS_MESSAGE = "模拟采购申请已提交。";

    private final PurchaseExecutionGateway purchaseGateway;
    private final PurchaseFactsService facts;
    private final PurchaseActionPayloadCodec payloadCodec;

    public PurchaseRequestActionHandler(PurchaseExecutionGateway purchaseGateway,
                                        PurchaseFactsService facts,
                                        PurchaseActionPayloadCodec payloadCodec) {
        this.purchaseGateway = purchaseGateway;
        this.facts = facts;
        this.payloadCodec = payloadCodec;
    }

    @Override
    public BusinessActionType supports() {
        return BusinessActionType.PURCHASE_REQUEST;
    }

    @Override
    public TaskType taskType() {
        return TaskType.PURCHASE_REQUEST;
    }

    @Override
    public TaskExecutionStatus statusAfterConfirmation() {
        return TaskExecutionStatus.COMPLETED;
    }

    @Override
    public Set<String> deterministicRegistrationRejectionCodes() {
        return Set.of("PURCHASE_FIELDS_REQUIRED", "PURCHASE_BUDGET_INVALID",
                "PURCHASE_BUDGET_NOT_FOUND", "PURCHASE_FACTS_MISMATCH",
                "PURCHASE_POLICY_DENIED", "PURCHASE_BUDGET_EXCEEDED");
    }

    @Override
    public Set<String> staleFailureCodes() {
        return Set.of("PURCHASE_FACTS_STALE", "PURCHASE_POLICY_STALE",
                "PURCHASE_BUDGET_STALE");
    }

    @Override
    public PendingPlan planPending(BusinessActionProposal proposal,
                                   VerifiedIdentity identity,
                                   LocalDate businessDate,
                                   Instant now) {
        if (!(proposal instanceof PurchaseActionProposal purchase)
                || purchase.actionType() != BusinessActionType.PURCHASE_REQUEST) {
            throw rule("PURCHASE_FIELDS_REQUIRED", "采购申请参数不完整。");
        }
        String itemName = normalize(purchase.itemName());
        String justification = normalize(purchase.justification());
        if (itemName == null || itemName.length() > 200
                || justification == null || justification.length() > 1000) {
            throw rule("PURCHASE_FIELDS_REQUIRED", "采购物品和 justification 不能为空且长度必须有效。");
        }
        BigDecimal requestedBudget = purchase.requestedBudget();
        if (requestedBudget == null || requestedBudget.signum() <= 0
                || requestedBudget.scale() > 2) {
            throw rule("PURCHASE_BUDGET_INVALID", "采购预算必须是两位小数以内的正数。");
        }
        BigDecimal availableBudget = facts.availableBudget(identity.employeeId());
        if (availableBudget == null) {
            throw rule("PURCHASE_BUDGET_NOT_FOUND", "当前员工没有可用的采购预算 fixture。");
        }
        if (purchase.availableBudget() == null
                || purchase.availableBudget().compareTo(availableBudget) != 0) {
            throw rule("PURCHASE_FACTS_MISMATCH", "采购 Proposal 的可用预算与 Java 权威事实不一致。");
        }
        if (requestedBudget.compareTo(availableBudget) > 0) {
            throw rule("PURCHASE_BUDGET_EXCEEDED", "采购申请超过当前可用预算。");
        }
        if (!"PASS".equals(purchase.policyResult())) {
            throw rule("PURCHASE_POLICY_DENIED", "采购 Proposal 未通过政策事实校验。");
        }
        PurchaseFactsService.PolicyEvaluation policy = facts.evaluatePolicy(
                itemName, requestedBudget, justification);
        if (!policy.allowed()) {
            throw rule(policy.code(), policy.message());
        }
        String payloadJson = payloadCodec.encode(new PurchaseActionPayload(
                1, itemName, requestedBudget, justification, availableBudget, "PASS"));
        return new PendingPlan(null, null, null, null, null, null, null, payloadJson);
    }

    @Override
    public String revalidateBeforeExecute(PendingAction action) {
        PurchaseActionPayload payload = payloadCodec.decode(action.actionPayloadJson());
        BigDecimal currentBudget = facts.availableBudget(action.employeeId());
        if (currentBudget == null) {
            return "PURCHASE_FACTS_STALE";
        }
        if (currentBudget.compareTo(payload.availableBudget()) != 0
                || payload.requestedBudget().compareTo(currentBudget) > 0) {
            return "PURCHASE_BUDGET_STALE";
        }
        if (!facts.evaluatePolicy(payload.itemName(), payload.requestedBudget(),
                payload.justification()).allowed()) {
            return "PURCHASE_POLICY_STALE";
        }
        return null;
    }

    @Override
    public ExecutionExecutionResult execute(PendingAction action, Instant now) {
        PurchaseActionPayload payload = payloadCodec.decode(action.actionPayloadJson());
        PurchaseExecutionResult execution = purchaseGateway.submit(new PurchaseSubmission(
                action.actionId(), action.ownerUserId(), action.employeeId(), payload.itemName(),
                payload.requestedBudget(), payload.justification(), now));
        return new ExecutionExecutionResult(execution.requestId(), SUCCESS_MESSAGE);
    }

    @Override
    public PendingActionView buildSummary(PendingAction action, String plaintextNonce) {
        PurchaseActionPayload payload = payloadCodec.decode(action.actionPayloadJson());
        return new PendingActionView(
                action.actionId(), action.actionType(), action.status(), TITLE,
                new PurchaseSummary(payload.itemName(), payload.requestedBudget(),
                        payload.justification(), payload.availableBudget(), payload.policyResult()),
                plaintextNonce, action.expiresAt(),
                action.status() == ActionStatus.PENDING_CONFIRMATION && plaintextNonce != null);
    }

    private static ActionException rule(String code, String message) {
        return new ActionException(HttpStatus.UNPROCESSABLE_ENTITY, code, message, null, null);
    }

    private static String normalize(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim();
        return normalized.isEmpty() ? null : normalized;
    }
}
