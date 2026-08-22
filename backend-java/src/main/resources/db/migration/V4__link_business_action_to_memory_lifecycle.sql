-- Memory 生命周期收口：PendingAction 需要关联到 ai_task_memory 的复合 key
-- (owner_user_id, conversation_id)，才能在确认 / 取消 / 过期时把 ACTIVE 任务记忆
-- 收口为 COMPLETED / ABANDONED，避免"动作已终结但 Memory 仍持续注入 Planner"。
--
-- 设计要点：
--   1. owner_user_id 是 Java 侧 VerifiedIdentity.userId()（Memory 表 user_id 维度），
--      不是 employee_id —— 两者在 Demo 模式下可能相同，但语义上必须存 Memory 的 owner key。
--   2. conversation_id 是该动作所在会话的 conversationId，与 X-Conversation-Id 一致。
--   3. 两列均允许 NULL：历史数据无关联信息，收口时跳过（不做数据回填，P0 范围外）。
ALTER TABLE business_action
    ADD COLUMN owner_user_id VARCHAR(64) NULL,
    ADD COLUMN conversation_id VARCHAR(64) NULL;
