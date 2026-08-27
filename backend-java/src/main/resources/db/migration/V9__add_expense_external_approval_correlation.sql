-- P3-5B1 durable Java-owned external approval correlation.  Historical rows stay NULL.
ALTER TABLE expense_claim ADD COLUMN external_provider VARCHAR(32) NULL;
ALTER TABLE expense_claim ADD COLUMN external_request_id VARCHAR(128) NULL;
ALTER TABLE expense_claim ADD COLUMN external_wait_id VARCHAR(80) NULL;

CREATE UNIQUE INDEX uq_expense_claim_external_request_id
    ON expense_claim(external_request_id) WHERE external_request_id IS NOT NULL;
CREATE UNIQUE INDEX uq_expense_claim_external_wait_id
    ON expense_claim(external_wait_id) WHERE external_wait_id IS NOT NULL;
