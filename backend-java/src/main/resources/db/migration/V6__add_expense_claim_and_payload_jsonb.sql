-- V6: P2-A Expense Workflow V1 - PendingAction 业务 payload 泛化
--
-- 目标：
--   1. 业务专属字段（start_date / end_date / half_day / reason / days /
--      balance_before / balance_after）从 business_action 核心字段下沉到
--      action_payload_json JSONB，避免 EXPENSE_CLAIM 复用 Leave 字段造假。
--   2. action_type CHECK 仅放宽到允许 'EXPENSE_CLAIM'；Phase 6 才允许
--      EXPENSE_CLAIM 真实持久化，本 Phase 仍只有 ANNUAL_LEAVE_REQUEST
--      数据（handler 校验层会把 EXPENSE_CLAIM 暂时挡在 Service 外）。
--   3. 历史 ANNUAL_LEAVE_REQUEST 数据 backfill 到 action_payload_json。
--   4. Leave 字段对 EXPENSE_CLAIM 解除 NOT NULL 强制：Drop NOT NULL，
--      由 action_type conditional CHECK 保证业务约束。
--   5. Repository 新写路径以 action_payload_json 为 canonical（Phase 5/6
--      落实）；本 Phase 不改 repository 写路径，backfill 仅为可读性。
--
-- 不引入：
--   - 业务专属表（expense_claim / expense_item 留 Phase 6）
--   - 任何 trigger / function / extension
--   - 修改 Memory 状态枚举或 AI Task Memory 业务

-- 1. 新增 action_payload_json JSONB
ALTER TABLE business_action
    ADD COLUMN action_payload_json JSONB NULL;

-- 2. 放宽 action_type CHECK：允许 EXPENSE_CLAIM
ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_type;
ALTER TABLE business_action
    ADD CONSTRAINT ck_business_action_type
    CHECK (action_type IN ('ANNUAL_LEAVE_REQUEST', 'EXPENSE_CLAIM'));

-- 3. 解除 Leave 字段 NOT NULL（EXPENSE_CLAIM 不需要这些字段）
ALTER TABLE business_action ALTER COLUMN start_date DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN end_date DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN half_day DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN reason DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN days DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN balance_before DROP NOT NULL;
ALTER TABLE business_action ALTER COLUMN balance_after DROP NOT NULL;

-- 4. action_type conditional CHECK：保证 ANNUAL_LEAVE_REQUEST 时 Leave
--    字段非空；EXPENSE_CLAIM 时这些字段为空。
ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_leave_required;
ALTER TABLE business_action ADD CONSTRAINT ck_business_action_leave_required
    CHECK (
        (action_type = 'ANNUAL_LEAVE_REQUEST'
            AND start_date IS NOT NULL
            AND end_date IS NOT NULL
            AND half_day IS NOT NULL
            AND reason IS NOT NULL
            AND days IS NOT NULL
            AND balance_before IS NOT NULL
            AND balance_after IS NOT NULL)
        OR
        (action_type = 'EXPENSE_CLAIM'
            AND start_date IS NULL
            AND end_date IS NULL
            AND half_day IS NULL
            AND reason IS NULL
            AND days IS NULL
            AND balance_before IS NULL
            AND balance_after IS NULL)
    );

-- 5. backfill 历史 ANNUAL_LEAVE_REQUEST 数据
UPDATE business_action
SET action_payload_json = jsonb_build_object(
    'startDate', start_date,
    'endDate', end_date,
    'halfDay', half_day,
    'reason', reason,
    'days', days,
    'balanceBefore', balance_before,
    'balanceAfter', balance_after,
    'source_action_id', action_id,
    'schemaVersion', 1
)
WHERE action_type = 'ANNUAL_LEAVE_REQUEST'
  AND action_payload_json IS NULL;

-- 6. ANNUAL_LEAVE_REQUEST 历史行 action_payload_json 必填（防御性）
ALTER TABLE business_action DROP CONSTRAINT IF EXISTS ck_business_action_payload_required;
ALTER TABLE business_action ADD CONSTRAINT ck_business_action_payload_required
    CHECK (
        (action_type = 'ANNUAL_LEAVE_REQUEST' AND action_payload_json IS NOT NULL)
        OR (action_type <> 'ANNUAL_LEAVE_REQUEST')
    );

-- 索引：action_payload_json GIN 索引（为后续按 payload 内容查询预留）
CREATE INDEX IF NOT EXISTS idx_business_action_payload_gin
    ON business_action USING GIN (action_payload_json);
