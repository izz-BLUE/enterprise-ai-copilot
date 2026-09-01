package com.fantuan.copilot.model.action;

public enum BusinessActionType {
    ANNUAL_LEAVE_REQUEST,
    // P2-A Expense Workflow V1: 报销业务动作（Phase 6 才允许真实持久化）。
    // Phase 4 仅在枚举与数据库 CHECK 中预登记，handler 校验层仍拒绝 EXPENSE_CLAIM
    // 路径（避免 Phase 4 期间误开 Expense 业务写入）。
    EXPENSE_CLAIM,
    PURCHASE_REQUEST
}
