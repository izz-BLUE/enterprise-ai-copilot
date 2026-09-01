package com.fantuan.copilot.model.action;

/**
 * Java -> Python HITL continuation delivery state.
 *
 * This is deliberately separate from the business action status: an action
 * can be EXPIRED while its checkpoint continuation is still pending delivery.
 */
public enum HitlReconciliationStatus {
    PENDING_RECONCILIATION,
    RECONCILED
}
