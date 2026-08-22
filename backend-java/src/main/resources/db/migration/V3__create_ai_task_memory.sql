-- Scoped Conversation Memory / Task Continuity P0
-- 表 ai_task_memory 承载"以 trusted user_id 为作用域 + conversation_id 为会话分组"的任务记忆。
-- 设计要点：
--   1. 主键为 (user_id, conversation_id)，保证同一用户同一会话只有一条记录
--   2. user_id 来自服务端 IdentityContext.require() 解析后的 VerifiedIdentity.userId()，
--      永远不接受客户端注入。employee_id 仍属于业务动作域，不作为本表 owner key。
--   3. 不存储 JWT、密码、internal token、nonce、idempotency_key 等敏感字段（由业务层把控）。
--   4. task_state_json / summary 设置固定最大长度作为基础边界，
--      当前阶段不引入内容 Safety Guard（不拦内容，只截字段长度）。
--   5. status 限定白名单；created_at / updated_at 由数据库默认 + 触发器维护 updated_at。

CREATE TABLE ai_task_memory (
    user_id          VARCHAR(64)  NOT NULL,
    conversation_id  VARCHAR(64)  NOT NULL,
    task_type        VARCHAR(64)  NOT NULL DEFAULT 'GENERIC',
    status           VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE',
    task_state_json  TEXT         NOT NULL DEFAULT '{}',
    summary          VARCHAR(500) NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_ai_task_memory PRIMARY KEY (user_id, conversation_id),
    CONSTRAINT ck_ai_task_memory_status
        CHECK (status IN ('ACTIVE', 'COMPLETED', 'ABANDONED')),
    CONSTRAINT ck_ai_task_memory_task_type_len
        CHECK (char_length(task_type) <= 64),
    CONSTRAINT ck_ai_task_memory_summary_len
        CHECK (char_length(summary) <= 500),
    CONSTRAINT ck_ai_task_memory_state_json_len
        CHECK (octet_length(task_state_json) <= 16384),
    CONSTRAINT ck_ai_task_memory_user_len
        CHECK (char_length(user_id) > 0 AND char_length(user_id) <= 64),
    CONSTRAINT ck_ai_task_memory_conversation_len
        CHECK (char_length(conversation_id) > 0 AND char_length(conversation_id) <= 64)
);

CREATE INDEX idx_ai_task_memory_user_updated
    ON ai_task_memory(user_id, updated_at DESC);