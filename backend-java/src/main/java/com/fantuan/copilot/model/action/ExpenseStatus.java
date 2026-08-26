package com.fantuan.copilot.model.action;

/**
 * 报销单业务状态（Java 权威 Source of Truth）。
 *
 * V2 §二十二：
 * - 状态定义完整（SUBMITTED / WAITING_APPROVAL / APPROVED / REJECTED / PAID）；
 * - 本轮只要求真实持久化 SUBMITTED（或 WAITING_APPROVAL），不开发真实审批引擎、
 *   不实现 Async Approval Engine / Event Bus / Workflow State Machine；
 * - 与 Memory lifecycle status（ACTIVE / COMPLETED / ABANDONED）完全分离，
 *   禁止把本枚举塞进 Memory 顶层生命周期状态。
 */
public enum ExpenseStatus {
    /** 已提交；本轮唯一真实持久化状态。 */
    SUBMITTED,
    /** 预留：等待审批。 */
    WAITING_APPROVAL,
    /** 预留：审批通过。 */
    APPROVED,
    /** 预留：审批拒绝。 */
    REJECTED,
    /** 预留：已支付。 */
    PAID
}
