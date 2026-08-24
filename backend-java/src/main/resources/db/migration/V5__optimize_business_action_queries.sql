CREATE UNIQUE INDEX ux_business_action_owner_conversation_active
    ON business_action(owner_user_id, conversation_id)
    WHERE owner_user_id IS NOT NULL
      AND conversation_id IS NOT NULL
      AND status IN ('PENDING_CONFIRMATION', 'PROCESSING');

CREATE INDEX idx_leave_request_employee_submitted
    ON leave_request(employee_id, submitted_at DESC);
