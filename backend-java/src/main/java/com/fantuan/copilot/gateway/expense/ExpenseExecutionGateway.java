package com.fantuan.copilot.gateway.expense;

/**
 * 报销执行网关（V2 §二十三）。
 *
 * BusinessActionService 不在同一事务内调用真实外部 OA；
 * PostgresExpenseSandboxGateway 为同库事务 sandbox 实现。
 * 真实 OA 需要 Outbox + 异步 + 外部幂等 + 回调/轮询/对账/补偿（V2 不实现）。
 */
public interface ExpenseExecutionGateway {

    /**
     * 创建 ExpenseClaim + ExpenseItem。
     * expensenId 由实现生成；source_action_id UNIQUE 保证不重复创建。
     */
    ExpenseExecutionResult submit(ExpenseSubmission submission);
}
