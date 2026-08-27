-- P3-5B2b durable authoritative-status check timestamp. Historical rows stay NULL.
ALTER TABLE expense_claim ADD COLUMN external_last_checked_at TIMESTAMPTZ NULL;

CREATE INDEX idx_expense_claim_external_reconciliation_due
    ON expense_claim (external_last_checked_at ASC NULLS FIRST, expense_id)
    WHERE status = 'WAITING_APPROVAL'
      AND external_provider = 'MOCK_OA'
      AND external_request_id IS NOT NULL;
