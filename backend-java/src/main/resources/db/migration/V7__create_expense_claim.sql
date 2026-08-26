-- V7: P2-A Expense Workflow V1 - Expense Claim / Item 业务表
--
-- V2 §二十一 / §二十二：
--   - expense_claim：报销单主表；source_action_id UNIQUE + FK（幂等防线）
--   - expense_item：费用项明细（FK ON DELETE CASCADE）
--   - expense_claim_number_seq：编号序列（EXP-YYYYMMDD-NNNNNN）
--   - 本轮仅 status='SUBMITTED' 被业务写入（其余枚举保留给后续审批）
--   - 不改动 ai_task_memory / business_action（后者已在 V6 泛化）

CREATE SEQUENCE expense_claim_number_seq START WITH 1;

CREATE TABLE expense_claim (
    expense_id VARCHAR(64) PRIMARY KEY,
    source_action_id VARCHAR(64) NOT NULL UNIQUE,
    employee_id VARCHAR(64) NOT NULL,
    trip_id VARCHAR(64) NOT NULL,
    cost_center VARCHAR(64) NOT NULL,
    claimed_amount NUMERIC(12,2) NOT NULL,
    reimbursable_amount NUMERIC(12,2) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_expense_claim_action FOREIGN KEY (source_action_id) REFERENCES business_action(action_id),
    CONSTRAINT ck_expense_claim_amounts CHECK (claimed_amount >= 0 AND reimbursable_amount >= 0 AND reimbursable_amount <= claimed_amount),
    CONSTRAINT ck_expense_claim_status CHECK (status IN ('SUBMITTED', 'WAITING_APPROVAL', 'APPROVED', 'REJECTED', 'PAID'))
);

CREATE TABLE expense_item (
    item_id BIGSERIAL PRIMARY KEY,
    expense_id VARCHAR(64) NOT NULL,
    invoice_id VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    description VARCHAR(200) NULL,
    CONSTRAINT fk_expense_item_claim FOREIGN KEY (expense_id) REFERENCES expense_claim(expense_id) ON DELETE CASCADE,
    CONSTRAINT ck_expense_item_amount CHECK (amount > 0),
    CONSTRAINT ck_expense_item_category CHECK (category IN ('HOTEL', 'TAXI', 'MEAL', 'TRAIN', 'FLIGHT')),
    CONSTRAINT uq_expense_item_invoice UNIQUE (expense_id, invoice_id)
);

CREATE INDEX idx_expense_claim_employee ON expense_claim(employee_id, created_at);
CREATE INDEX idx_expense_claim_status ON expense_claim(status);
