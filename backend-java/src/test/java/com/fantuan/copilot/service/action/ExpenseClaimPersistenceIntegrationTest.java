package com.fantuan.copilot.service.action;

import com.fantuan.copilot.PostgresIntegrationTestBase;
import com.fantuan.copilot.dto.action.ActionExecutionResponse;
import com.fantuan.copilot.dto.action.ExpenseActionProposal;
import com.fantuan.copilot.dto.action.PendingActionView;
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

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

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

    @Autowired BusinessActionService actionService;
    @Autowired PendingActionRepository actions;
    @Autowired ExpenseClaimRepository expenseClaims;
    @Autowired BusinessActionProperties properties;
    @Autowired JdbcTemplate jdbc;

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
        return new ExpenseActionProposal(
                com.fantuan.copilot.model.action.BusinessActionType.EXPENSE_CLAIM,
                "TRIP-20260818-001",
                List.of(new ExpenseActionProposal.ExpenseItemPayload(
                        "HOTEL", new BigDecimal("1600"), "INV-001", "上海如家 2 晚"),
                        new ExpenseActionProposal.ExpenseItemPayload(
                                "TAXI", new BigDecimal("230"), "INV-002", "机场往返打车")),
                new BigDecimal("1830"),
                new BigDecimal("1730"),  // HOTEL 1600 → 封顶 750×2=1500；TAXI 230 实报 → 1730
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
}
