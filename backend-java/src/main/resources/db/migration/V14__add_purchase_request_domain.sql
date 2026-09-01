-- P4-3 Purchase Extension Proof: third controlled business domain.

ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_type;
ALTER TABLE business_action
    ADD CONSTRAINT ck_business_action_type
    CHECK (action_type IN ('ANNUAL_LEAVE_REQUEST', 'EXPENSE_CLAIM', 'PURCHASE_REQUEST'));

ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_leave_required;
ALTER TABLE business_action ADD CONSTRAINT ck_business_action_leave_required
    CHECK (
        (action_type = 'ANNUAL_LEAVE_REQUEST'
            AND start_date IS NOT NULL AND end_date IS NOT NULL AND half_day IS NOT NULL
            AND reason IS NOT NULL AND days IS NOT NULL
            AND balance_before IS NOT NULL AND balance_after IS NOT NULL)
        OR
        (action_type IN ('EXPENSE_CLAIM', 'PURCHASE_REQUEST')
            AND start_date IS NULL AND end_date IS NULL AND half_day IS NULL
            AND reason IS NULL AND days IS NULL
            AND balance_before IS NULL AND balance_after IS NULL)
    );

ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_payload_required;
ALTER TABLE business_action ADD CONSTRAINT ck_business_action_payload_required
    CHECK (
        (action_type IN ('ANNUAL_LEAVE_REQUEST', 'PURCHASE_REQUEST')
            AND action_payload_json IS NOT NULL)
        OR (action_type = 'EXPENSE_CLAIM')
    );

ALTER TABLE task_execution DROP CONSTRAINT IF EXISTS ck_task_execution_type;
ALTER TABLE task_execution
    ADD CONSTRAINT ck_task_execution_type
    CHECK (task_type IN ('LEAVE_REQUEST', 'EXPENSE_CLAIM', 'PURCHASE_REQUEST'));

CREATE SEQUENCE purchase_request_number_seq START WITH 1;

CREATE TABLE purchase_request (
    request_id VARCHAR(64) PRIMARY KEY,
    source_action_id VARCHAR(64) NOT NULL UNIQUE,
    owner_user_id VARCHAR(64) NULL,
    employee_id VARCHAR(64) NOT NULL,
    item_name VARCHAR(200) NOT NULL,
    requested_budget NUMERIC(12,2) NOT NULL,
    justification VARCHAR(1000) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_purchase_request_action FOREIGN KEY (source_action_id)
        REFERENCES business_action(action_id),
    CONSTRAINT ck_purchase_request_budget CHECK (requested_budget > 0),
    CONSTRAINT ck_purchase_request_status CHECK (status IN ('SUBMITTED')),
    CONSTRAINT ck_purchase_request_text CHECK (length(btrim(item_name)) > 0
        AND length(btrim(justification)) > 0)
);

CREATE INDEX idx_purchase_request_employee ON purchase_request(employee_id, created_at);
CREATE INDEX idx_purchase_request_owner ON purchase_request(owner_user_id, created_at);
