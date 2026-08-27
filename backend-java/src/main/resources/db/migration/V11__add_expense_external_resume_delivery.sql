-- P3-5B3 durable Java -> Python external resume delivery markers.
-- Terminal ExpenseClaim state remains authoritative; these fields only track
-- delivery attempts and successful acknowledgement.
ALTER TABLE expense_claim ADD COLUMN external_resume_last_attempt_at TIMESTAMPTZ NULL;
ALTER TABLE expense_claim ADD COLUMN external_resume_completed_at TIMESTAMPTZ NULL;

CREATE INDEX idx_expense_claim_external_resume_due
    ON expense_claim (external_resume_last_attempt_at ASC NULLS FIRST, expense_id)
    WHERE status IN ('APPROVED', 'REJECTED')
      AND external_provider = 'MOCK_OA'
      AND external_request_id IS NOT NULL
      AND external_wait_id IS NOT NULL
      AND external_resume_completed_at IS NULL;
