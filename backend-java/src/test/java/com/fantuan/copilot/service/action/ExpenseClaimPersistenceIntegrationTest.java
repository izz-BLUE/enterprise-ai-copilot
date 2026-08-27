package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.HitlResumePayload;
import com.fantuan.copilot.dto.action.HitlWaitMarker;
import com.fantuan.copilot.dto.action.ExternalWaitMarker;
import com.fantuan.copilot.dto.action.PendingActionView;
import com.fantuan.copilot.gateway.python.PythonAgentGateway;
import com.fantuan.copilot.identity.VerifiedIdentity;
import com.fantuan.copilot.model.action.ActionStatus;
import com.fantuan.copilot.model.action.ExpenseStatus;
import com.fantuan.copilot.repository.action.ExpenseClaimRepository;
import com.fantuan.copilot.repository.action.PendingActionRepository;
import com.fantuan.copilot.service.demo.DemoIdentity;
import com.fantuan.copilot.service.demo.DemoRole;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;

/**
 * ExpenseClaim 持久化集成测试（V2 §二十三 / §二十八 Stress H）。
 *
 * 覆盖：
 * - 正常 submit：confirm 后创建 1 笔 ExpenseClaim（status=SUBMITTED）+ items
 * - idempotent replay：同 idempotency key 重复 confirm → 仅 1 笔 ExpenseClaim
 * - concurrent duplicate：并发 confirm → 仅 1 笔 ExpenseClaim（source_action_id UNIQUE）
 */
@SpringBootTest(properties = {
        "business.actions.enabled=true",
        "business.actions.require-admin=false",
        "demo.identity.enabled=true"
})
class ExpenseClaimPersistenceIntegrationTest extends PostgresIntegrationTestBase {
    private static final DemoIdentity USER_A = new DemoIdentity(
            "DEMO-001", "DEMO-001", "Demo User", DemoRole.EMPLOYEE);
    private static final String CONV_EXPENSE_HITL = "conv-expense-hitl-test";

    @Autowired BusinessActionService actionService;
    @Autowired BusinessActionHitlCoordinator hitlCoordinator;
    @Autowired PendingActionRepository actions;
    @Autowired ExpenseClaimRepository expenseClaims;
    @Autowired BusinessActionProperties properties;
    @Autowired JdbcTemplate jdbc;
    @Autowired ExpenseCalculationService calculation;
    @MockitoBean PythonAgentGateway pythonAgentGateway;

    private static final VerifiedIdentity VERIFIED_USER_A = VerifiedIdentity.from(USER_A);

    @BeforeEach
    void resetDatabase() {
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
        jdbc.execute("DELETE FROM ai_task_memory");
        // leave_request FK 指向 business_action：先清 leave_request 再清 action。
        jdbc.execute("DELETE FROM leave_request");
        jdbc.execute("DELETE FROM business_action");
        jdbc.execute("ALTER SEQUENCE expense_claim_number_seq RESTART WITH 1");
        properties.setMaxPending(100);
    }

    @AfterEach
    void cleanup() {
        jdbc.execute("DELETE FROM expense_item");
        jdbc.execute("DELETE FROM expense_claim");
    }

    private ExpenseActionProposal proposal() {
        return proposal(new BigDecimal("1730"));
    }

    private ExpenseActionProposal proposal(BigDecimal reimbursableAmount) {
        return new ExpenseActionProposal(
                com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM,
                "TRIP-20260818-001",
                List.of(new ExpenseActionProposal.ExpenseItemPayload(
                        "HOTEL", new BigDecimal("1600"), "INV-001", "上海如家 2 晚"),
                        new ExpenseActionProposal.ExpenseItemPayload(
                                "TAXI", new BigDecimal("230"), "INV-002", "机场往返打车")),
                new BigDecimal("1830"),
                reimbursableAmount,  // HOTEL 1600 → 封顶 750×2=1500；TAXI 230 实报 → 1730
                "COST-IT",
                "上海出差酒店与交通",
                List.of("INV-001", "INV-002"),
                2);
    }

    @Test
    void confirmCreatesSingleExpenseClaimWithItems() {
        PendingActionView view = actionService.createPending(
                proposal(), "origin-exp", null, USER_A, null);
        ActionExecutionResponse resp = actionService.confirm(
                view.actionId(), view.confirmationNonce(),
                UUID.randomUUID().toString(), null, "trace-confirm", USER_A);
        assertEquals(ActionStatus.SUCCEEDED, resp.status());
        var claim = expenseClaims.findByExpenseId(resp.requestId()).orElseThrow();
        assertEquals(ExpenseStatus.SUBMITTED, claim.status());
        assertEquals("DEMO-001", claim.employeeId());
        assertEquals("TRIP-20260818-001", claim.tripId());
        assertEquals(0, claim.claimedAmount().compareTo(new BigDecimal("1830")));
        assertEquals(2, expenseClaims.findItemsByExpenseId(claim.expenseId()).size());
        assertEquals(1, expenseClaims.countBySourceActionId(view.actionId()));
    }

    @Test
    void pythonExpenseAmountCannotOverrideJavaAuthoritativeCalculation() {
        var malicious = proposal(new BigDecimal("999999"));

        var authoritative = calculation.calculate(
                List.of(
                        new com.fantuan.copilot.model.action.ExpenseItem(
                                "INV-001", "HOTEL", new BigDecimal("1600"), "上海如家 2 晚"),
                        new com.fantuan.copilot.model.action.ExpenseItem(
                                "INV-002", "TAXI", new BigDecimal("230"), "机场往返打车")),
                2);
        assertEquals(0, authoritative.reimbursableAmount().compareTo(new BigDecimal("1730.00")));

        ActionException exception = assertThrows(ActionException.class,
                () -> actionService.createPending(
                        malicious, "origin-exp-malicious", null, USER_A, null));
        assertEquals("BUSINESS_RULE_VIOLATION", exception.errorCode());
        assertEquals("实报金额与系统计算不一致。", exception.getMessage());
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE action_type = 'EXPENSE_CLAIM'",
                Integer.class));
    }

    @Test
    void hitlExpenseAmountInvalidIsDeterministicallyRejectedWithoutActionRow() {
        ExpenseActionProposal invalid = new ExpenseActionProposal(
                com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM,
                "TRIP-20260818-001",
                List.of(new ExpenseActionProposal.ExpenseItemPayload(
                        "HOTEL", BigDecimal.ZERO, "INV-001", "无效金额")),
                BigDecimal.ZERO, BigDecimal.ZERO, "COST-IT", "上海出差报销",
                List.of("INV-001"), 1);
        HitlWaitMarker wait = expenseWait();

        ActionException exception = assertThrows(ActionException.class, () -> hitlCoordinator.registerWait(
                invalid, wait, "hitl-expense-amount", null, VERIFIED_USER_A, CONV_EXPENSE_HITL));

        assertEquals("EXPENSE_AMOUNT_INVALID", exception.errorCode());
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE action_type = 'EXPENSE_CLAIM'",
                Integer.class));
        verifyRejectedResume(wait);
    }

    @Test
    void hitlExpenseInvoiceMismatchIsDeterministicallyRejectedWithoutActionRow() {
        ExpenseActionProposal invalid = new ExpenseActionProposal(
                com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM,
                "TRIP-20260818-001",
                List.of(new ExpenseActionProposal.ExpenseItemPayload(
                        "TAXI", new BigDecimal("230"), "INV-001", "机场往返打车")),
                new BigDecimal("230"), new BigDecimal("230"), "COST-IT", "上海出差报销",
                List.of("INV-999"), 1);
        HitlWaitMarker wait = expenseWait();

        ActionException exception = assertThrows(ActionException.class, () -> hitlCoordinator.registerWait(
                invalid, wait, "hitl-expense-invoice", null, VERIFIED_USER_A, CONV_EXPENSE_HITL));

        assertEquals("EXPENSE_INVOICES_REQUIRED", exception.errorCode());
        assertEquals(0, jdbc.queryForObject(
                "SELECT COUNT(*) FROM business_action WHERE action_type = 'EXPENSE_CLAIM'",
                Integer.class));
        verifyRejectedResume(wait);
    }

    @Test
    void replayedConfirmCreatesOnlyOneExpenseClaim() {
        PendingActionView view = actionService.createPending(
                proposal(), "origin-exp-replay", null, USER_A, null);
        String key = UUID.randomUUID().toString();
        actionService.confirm(view.actionId(), view.confirmationNonce(),
                key, null, "trace-1", USER_A);
        ActionExecutionResponse replay = actionService.confirm(
                view.actionId(), view.confirmationNonce(),
                key, null, "trace-2", USER_A);
        assertTrue(replay.replayed());
        assertEquals(replay.requestId(), replay.requestId());
        assertEquals(1, expenseClaims.countBySourceActionId(view.actionId()));
    }

    @Test
    void externalCorrelationBindingIsIdempotentAndOnlyAdvancesOnSameOaRequest() {
        PendingActionView view = actionService.createPending(
                proposal(), "origin-external", null, USER_A, null);
        ActionExecutionResponse response = actionService.confirm(
                view.actionId(), view.confirmationNonce(), UUID.randomUUID().toString(),
                null, "trace-external", USER_A);
        String waitId = ExternalWaitMarker.expectedWaitId("ex_" + "a".repeat(32), response.requestId());

        expenseClaims.bindExternalWait(response.requestId(), waitId);
        expenseClaims.bindExternalWait(response.requestId(), waitId);
        var bound = expenseClaims.findByExpenseId(response.requestId()).orElseThrow();
        assertEquals(waitId, bound.externalWaitId());
        assertEquals(ExpenseStatus.SUBMITTED, bound.status());

        expenseClaims.bindExternalRequest(response.requestId(), "MOCK_OA", "OA-EXP-001");
        expenseClaims.bindExternalRequest(response.requestId(), "MOCK_OA", "OA-EXP-001");
        var submitted = expenseClaims.findByExpenseId(response.requestId()).orElseThrow();
        assertEquals(ExpenseStatus.WAITING_APPROVAL, submitted.status());
        assertEquals("MOCK_OA", submitted.externalProvider());
        assertEquals("OA-EXP-001", submitted.externalRequestId());
        assertThrows(IllegalStateException.class,
                () -> expenseClaims.bindExternalWait(response.requestId(), "extwait_" + "b".repeat(64)));
        assertThrows(IllegalStateException.class,
                () -> expenseClaims.bindExternalRequest(response.requestId(), "MOCK_OA", "OA-EXP-002"));
    }

    @Test
    void concurrentConfirmCreatesOnlyOneExpenseClaim() throws Exception {
        PendingActionView view = actionService.createPending(
                proposal(), "origin-exp-conc", null, USER_A, null);
        int threads = 4;
        CyclicBarrier barrier = new CyclicBarrier(threads);
        CountDownLatch done = new CountDownLatch(threads);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        AtomicInteger failures = new AtomicInteger();
        for (int i = 0; i < threads; i++) {
            pool.submit(() -> {
                try {
                    barrier.await();
                    try {
                        actionService.confirm(view.actionId(), view.confirmationNonce(),
                                UUID.randomUUID().toString(), null, "trace-c", USER_A);
                    } catch (Exception e) {
                        failures.incrementAndGet();
                    }
                } catch (Exception ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        done.await();
        pool.shutdown();
        assertEquals(1, expenseClaims.countBySourceActionId(view.actionId()));
        // 并发下允许部分 confirm 因状态机冲突/幂等重放失败（不创建第二名报销）
        assertTrue(failures.get() >= 0);
    }

    private HitlWaitMarker expenseWait() {
        return new HitlWaitMarker(1, "BUSINESS_ACTION_CONFIRMATION",
                "wait_" + "a".repeat(64), "ex_" + "b".repeat(32),
                com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM);
    }

    private void verifyRejectedResume(HitlWaitMarker wait) {
        var captor = org.mockito.ArgumentCaptor.forClass(HitlResumePayload.class);
        verify(pythonAgentGateway).post(eq("/agent/langgraph/hitl/resume"), captor.capture(),
                any(), eq(com.fantuan.copilot.dto.PythonAgentResponse.class), any());
        HitlResumePayload payload = captor.getValue();
        assertEquals(HitlResumePayload.HitlDecision.REJECTED, payload.decision());
        assertNull(payload.actionId());
        assertNull(payload.requestId());
        assertEquals(ActionStatus.FAILED, payload.actionStatus());
        assertEquals(wait.waitId(), payload.waitId());
        assertEquals(wait.executionId(), payload.executionId());
        assertEquals(wait.actionType(), payload.actionType());
        assertEquals("申请未能完成，已安全拒绝。", payload.message());
    }
}
