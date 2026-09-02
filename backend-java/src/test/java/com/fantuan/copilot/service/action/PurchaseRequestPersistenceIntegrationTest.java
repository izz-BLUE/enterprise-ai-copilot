package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.auth.AuthRole;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.dto.action.PurchaseActionProposal;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.BusinessActionType;
import com.fantuan.copilot.model.action.PurchaseRequestStatus;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.repository.action.PurchaseRequestRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = {
        "demo.auth.enabled=true",
        "demo.auth.default-password=test-password",
        "demo.auth.public-password=public-test-password",
        "demo.auth.interview-password=interview-test-password",
        "demo.auth.admin-password=admin-test-password",
        "business.actions.enabled=true",
        "business.actions.require-admin=false"
})
class PurchaseRequestPersistenceIntegrationTest extends PostgresIntegrationTestBase {
    private static final VerifiedIdentity USER_A = new VerifiedIdentity(
            "U10001", "zhangsan", "E10001", "张三", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);
    private static final VerifiedIdentity USER_B = new VerifiedIdentity(
            "U10002", "lisi", "E10002", "李四", AuthRole.EMPLOYEE, true,
            VerifiedIdentity.Source.JWT);

    @Autowired BusinessActionService actionService;
    @Autowired PurchaseRequestRepository purchaseRequests;
    @Autowired PendingActionRepository actions;
    @Autowired BusinessActionProperties properties;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM purchase_request");
        jdbc.execute("DELETE FROM task_execution");
        jdbc.execute("DELETE FROM ai_task_memory");
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE purchase_request_number_seq RESTART WITH 1");
        properties.setEnabled(true);
        properties.setRequireAdmin(false);
    }

    @Test
    void confirmCreatesOnePurchaseRequestAndMarksActionSucceeded() {
        PendingActionView pending = actionService.createPending(
                proposal(new BigDecimal("6800.00")), "purchase-origin", null, USER_A, "purchase-conv");

        ActionExecutionResponse response = actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), UUID.randomUUID().toString(),
                null, "purchase-confirm", USER_A);

        assertEquals(ActionStatus.SUCCEEDED, response.status());
        assertEquals(BusinessActionType.PURCHASE_REQUEST, response.type());
        var request = purchaseRequests.findByRequestId(response.requestId()).orElseThrow();
        assertEquals(PurchaseRequestStatus.SUBMITTED, request.status());
        assertEquals(USER_A.userId(), request.ownerUserId());
        assertEquals(USER_A.employeeId(), request.employeeId());
        assertEquals("MacBook Pro", request.itemName());
        assertEquals(0, request.requestedBudget().compareTo(new BigDecimal("6800.00")));
        assertEquals(1, purchaseRequests.countBySourceActionId(pending.actionId()));
        assertEquals(ActionStatus.SUCCEEDED, actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(*) FROM task_execution WHERE action_id = ?",
                Integer.class, pending.actionId()));
    }

    @Test
    void idempotentConfirmDoesNotDuplicatePurchaseRequest() {
        PendingActionView pending = actionService.createPending(
                proposal(new BigDecimal("6800.00")), "purchase-replay", null, USER_A, null);
        String idempotencyKey = UUID.randomUUID().toString();

        actionService.confirm(pending.actionId(), pending.confirmationNonce(), idempotencyKey,
                null, "purchase-first", USER_A);
        ActionExecutionResponse replay = actionService.confirm(
                pending.actionId(), pending.confirmationNonce(), idempotencyKey,
                null, "purchase-replay-confirm", USER_A);

        assertTrue(replay.replayed());
        assertEquals(1, purchaseRequests.countBySourceActionId(pending.actionId()));
    }

    @Test
    void wrongEmployeeCannotConfirmPurchaseAction() {
        PendingActionView pending = actionService.createPending(
                proposal(new BigDecimal("6800.00")), "purchase-wrong-user", null, USER_A, null);

        ActionException exception = assertThrows(ActionException.class,
                () -> actionService.confirm(pending.actionId(), pending.confirmationNonce(),
                        UUID.randomUUID().toString(), null, "purchase-wrong-confirm", USER_B));

        assertEquals("ACTION_NOT_FOUND", exception.errorCode());
        assertEquals(0, purchaseRequests.countBySourceActionId(pending.actionId()));
    }

    @Test
    void javaRejectsMismatchedPythonBudgetBeforePendingAction() {
        ActionException exception = assertThrows(ActionException.class,
                () -> actionService.createPending(new PurchaseActionProposal(
                        BusinessActionType.PURCHASE_REQUEST, "MacBook Pro",
                        new BigDecimal("6800.00"), "开发工作", new BigDecimal("1.00"), "PASS"),
                        "purchase-mismatch", null, USER_A, null));

        assertEquals("PURCHASE_FACTS_MISMATCH", exception.errorCode());
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE action_type = 'PURCHASE_REQUEST'",
                Integer.class));
    }

    @Test
    void purchaseInternalStaleCodesAreNotAcceptedAsExternalFailureCapability() {
        PendingActionView pending = actionService.createPending(
                proposal(new BigDecimal("6800.00")), "purchase-stale-metadata", null,
                USER_A, null);

        assertThrows(IllegalArgumentException.class, () -> actionService.failStaleConfirmation(
                pending.actionId(), pending.confirmationNonce(), null,
                "purchase-stale-metadata-confirm", USER_A, "PURCHASE_POLICY_STALE"));

        assertEquals(ActionStatus.PENDING_CONFIRMATION,
                actions.find(pending.actionId()).orElseThrow().status());
        assertEquals(0, purchaseRequests.countBySourceActionId(pending.actionId()));
    }

    private static PurchaseActionProposal proposal(BigDecimal budget) {
        return new PurchaseActionProposal(BusinessActionType.PURCHASE_REQUEST,
                "MacBook Pro", budget, "开发工作", new BigDecimal("20000.00"), "PASS");
    }
}
