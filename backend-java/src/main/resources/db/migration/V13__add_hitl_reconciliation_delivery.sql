-- Keep business action status and Java -> Python continuation delivery state
-- separate. Existing terminal rows are left NULL and require an explicit,
-- separately audited historical recovery decision.
ALTER TABLE business_action
    ADD COLUMN hitl_reconciliation_status VARCHAR(32) NULL;

ALTER TABLE business_action
    ADD CONSTRAINT ck_business_action_hitl_reconciliation_status
    CHECK (hitl_reconciliation_status IN ('PENDING_RECONCILIATION', 'RECONCILED')
           OR hitl_reconciliation_status IS NULL);

CREATE INDEX idx_business_action_hitl_reconciliation_due
    ON business_action (completed_at ASC NULLS FIRST, action_id)
    WHERE status = 'EXPIRED'
      AND agent_execution_id IS NOT NULL
      AND hitl_wait_id IS NOT NULL
      AND hitl_reconciliation_status = 'PENDING_RECONCILIATION';
