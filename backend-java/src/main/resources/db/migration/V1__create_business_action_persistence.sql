CREATE TABLE business_action_control (
    control_key VARCHAR(32) PRIMARY KEY,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO business_action_control(control_key) VALUES ('GLOBAL');

CREATE TABLE leave_account (
    employee_id VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(120) NOT NULL,
    annual_balance NUMERIC(8,1) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_leave_account_balance CHECK (annual_balance >= 0)
);

CREATE TABLE business_action (
    action_id VARCHAR(64) PRIMARY KEY,
    action_type VARCHAR(64) NOT NULL,
    origin_trace_id VARCHAR(128) NOT NULL,
    employee_id VARCHAR(64) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    half_day VARCHAR(16) NOT NULL,
    reason VARCHAR(200) NOT NULL,
    days NUMERIC(8,1) NOT NULL,
    balance_before NUMERIC(8,1) NOT NULL,
    balance_after NUMERIC(8,1) NOT NULL,
    confirmation_nonce_digest BYTEA NOT NULL,
    status VARCHAR(32) NOT NULL,
    idempotency_key UUID NULL,
    request_id VARCHAR(64) NULL,
    execution_message VARCHAR(255) NULL,
    failure_code VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_business_action_nonce_digest CHECK (octet_length(confirmation_nonce_digest) = 32),
    CONSTRAINT ck_business_action_days CHECK (days > 0),
    CONSTRAINT ck_business_action_balances CHECK (balance_before >= 0 AND balance_after >= 0),
    CONSTRAINT ck_business_action_dates CHECK (end_date >= start_date),
    CONSTRAINT ck_business_action_half_day CHECK (half_day IN ('NONE', 'AM', 'PM')),
    CONSTRAINT ck_business_action_status CHECK (status IN ('PENDING_CONFIRMATION', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED')),
    CONSTRAINT ck_business_action_type CHECK (action_type = 'ANNUAL_LEAVE_REQUEST')
);

CREATE INDEX idx_business_action_status_expires ON business_action(status, expires_at);
CREATE INDEX idx_business_action_completed ON business_action(status, completed_at);
CREATE INDEX idx_business_action_employee ON business_action(employee_id, created_at);

CREATE SEQUENCE leave_request_number_seq START WITH 1;

CREATE TABLE leave_request (
    request_id VARCHAR(64) PRIMARY KEY,
    source_action_id VARCHAR(64) NOT NULL UNIQUE,
    employee_id VARCHAR(64) NOT NULL,
    leave_type VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    half_day VARCHAR(16) NOT NULL,
    days NUMERIC(8,1) NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_leave_request_action FOREIGN KEY (source_action_id) REFERENCES business_action(action_id),
    CONSTRAINT ck_leave_request_type CHECK (leave_type = 'ANNUAL'),
    CONSTRAINT ck_leave_request_half_day CHECK (half_day IN ('NONE', 'AM', 'PM')),
    CONSTRAINT ck_leave_request_days CHECK (days > 0),
    CONSTRAINT ck_leave_request_dates CHECK (end_date >= start_date)
);

CREATE INDEX idx_leave_request_employee_dates ON leave_request(employee_id, start_date, end_date);
