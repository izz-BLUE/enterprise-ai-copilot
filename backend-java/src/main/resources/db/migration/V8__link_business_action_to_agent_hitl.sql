-- P3-4: correlate the Java business-action authority with one persisted
-- Planner-first HITL wait.  Historical actions remain legacy rows with NULL
-- metadata and keep their existing confirm/cancel semantics.
ALTER TABLE business_action
    ADD COLUMN agent_execution_id VARCHAR(40) NULL,
    ADD COLUMN hitl_wait_id VARCHAR(80) NULL;

CREATE UNIQUE INDEX uq_business_action_hitl_wait_id
    ON business_action(hitl_wait_id)
    WHERE hitl_wait_id IS NOT NULL;
