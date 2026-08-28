-- Java Task Runtime: durable ordering and lifecycle for the bounded two-task group.
-- task_execution is orchestration state only; LeaveRequest, ExpenseClaim and
-- PendingAction remain the business authorities for their respective domains.
CREATE TABLE task_execution (
    task_group_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) PRIMARY KEY,
    owner_user_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    sequence_no SMALLINT NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    task_text VARCHAR(2000) NOT NULL,
    clarification_context VARCHAR(4000) NULL,
    status VARCHAR(32) NOT NULL,
    action_id VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_task_execution_group_sequence UNIQUE (task_group_id, sequence_no),
    CONSTRAINT fk_task_execution_action FOREIGN KEY (action_id)
        REFERENCES business_action(action_id) ON DELETE SET NULL,
    CONSTRAINT ck_task_execution_sequence CHECK (sequence_no IN (1, 2)),
    CONSTRAINT ck_task_execution_type CHECK (task_type IN ('LEAVE_REQUEST', 'EXPENSE_CLAIM')),
    CONSTRAINT ck_task_execution_status CHECK (status IN (
        'PENDING', 'RUNNING', 'WAITING_CLARIFICATION', 'WAITING_USER',
        'WAITING_EXTERNAL', 'COMPLETED', 'FAILED', 'CANCELLED',
        'EXPIRED', 'REJECTED')),
    CONSTRAINT ck_task_execution_text CHECK (length(btrim(task_text)) > 0)
);

CREATE UNIQUE INDEX uq_task_execution_action_id
    ON task_execution(action_id) WHERE action_id IS NOT NULL;

CREATE INDEX idx_task_execution_owner_conversation
    ON task_execution(owner_user_id, conversation_id, status, sequence_no);

CREATE INDEX idx_task_execution_group_sequence
    ON task_execution(task_group_id, sequence_no);
