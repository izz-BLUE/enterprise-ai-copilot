# Scoped Conversation Memory P0 架构说明

本文是当前运行时基线。Memory 只保存同一用户、同一会话中的未完成任务状态，
不是用户画像、偏好库、权限缓存或业务事实来源。

## 1. 核心边界

- 唯一作用域是 Java 认证上下文派生的 `(user_id, conversation_id)`。
- `user_id` 只来自 `VerifiedIdentity`；Python、LLM、前端和 Memory 内容都不能提供 owner。
- `conversation_id` 由 Java 校验客户端分组 hint，非法或缺失时生成 UUID。
- Python 只生成非权威内容提案；Java 固定按 `UPSERT + ACTIVE` 落库。
- `COMPLETED / ABANDONED` 只由 Java `PendingAction` 生命周期收口。
- MemoryContext 是不可信历史数据，不扩大 Tool 可见集合，不进入受信任 Tool 参数。

## 2. Read Path

```text
Frontend conversationId
  → Java LangGraphAgentController
  → IdentityContext.require(request)
  → AiTaskMemoryService.find(trustedUserId, conversationId)
  → 仅 ACTIVE 转为 InternalAgentChatRequest.MemoryContextView
  → Python /agent/langgraph/chat
  → Planner prompt 的“不可信历史任务上下文”区块
```

Java 在调用 Python 之前读取数据库。读库失败按“无 Memory”继续，终态记录不会注入
Planner。公共 `ChatRequest` 不暴露 `memoryContext` 字段。

## 3. Write Path

```text
run_langgraph_agent result
  → MemoryTriggerPolicy
  → MemoryExtractor
  → MemoryWritePolicy
      - trusted key 递归剥离
      - 敏感字符串脱敏
      - task state 16 KiB / summary 500 字符
      - 只允许 UPSERT + ACTIVE
  → MEMORY_WRITE_MODE
      - DISABLED: 入口短路，不构造 Pipeline
      - AUDIT_ONLY: 运行并审计，不输出提案
      - ENABLED: 写入 AgentResponse.memory_proposal
  → Java LangGraphAgentController
      - 有 action_proposal: 先成功创建 PendingAction
      - 再用当前 VerifiedIdentity + conversationId 持久化 ACTIVE Memory
      - 无 action_proposal: 直接持久化 ACTIVE Memory
```

`memory_proposal` 只有以下字段：

```json
{
  "task_type": "LEAVE_REQUEST",
  "task_state": {"waiting_for": "date"},
  "summary": "等待用户补充请假日期"
}
```

它不包含 `user_id`、`conversation_id`、`action` 或 `status`。Java 的
`AiTaskMemoryService.upsertActiveFromAgent` 再做 trusted-key、生命周期字段、敏感内容
和大小校验，然后以固定 `TaskStatus.ACTIVE` 执行单条条件 UPSERT。

## 4. 与 PendingAction 的顺序

带业务动作的响应严格按以下顺序处理：

1. Java 验证当前请求是否允许业务动作；
2. `BusinessActionService.createPending` 重新执行权限、字段、余额、冲突、容量和同会话活动动作校验；
3. PendingAction 成功持久化；
4. Java 才接受本次 `memory_proposal`。

因此，权限拒绝、业务规则失败、容量失败或 `ACTION_CONVERSATION_IN_PROGRESS` 都不会把
本次提案覆盖到既有会话 Memory。Memory 持久化是主响应旁路：失败会记录安全日志，
不会撤销已经创建的 PendingAction，也不会把 Python 内容提升为业务事实。

## 5. 终态收口

`BusinessActionService` 在 PendingAction 状态变化的同一事务内调用：

- 确认并执行成功：`AiTaskMemoryService.complete`；
- 用户取消、TTL 过期或已创建动作处理失败：`AiTaskMemoryService.abandon`。

无 Memory 或已经进入另一终态时，收口调用无副作用。终态记录不能被后续 Agent 提案
重新激活。

## 6. 模块职责

| 侧 | 模块 | 职责 |
| --- | --- | --- |
| Python | `memory_trigger_policy.py` | 确定性判断是否值得提取 |
| Python | `memory_extractor.py` | 将白名单输入解析为 `MemoryProposal` |
| Python | `memory_write_policy.py` | trusted-key 清洗、脱敏、大小和 ACTIVE-only 约束 |
| Python | `memory_pipeline.py` | Trigger → Extractor → Policy 编排 |
| Python | `memory_runtime_hook.py` | 出口旁路、审计、fail-safe |
| Python | `chat_schema.py` | `AgentMemoryProposal` 响应契约 |
| Java | `LangGraphAgentController` | 当前认证作用域、动作优先顺序、提案持久化编排 |
| Java | `AiTaskMemoryService` | Java 独立内容校验与固定 ACTIVE 写入 |
| Java | `BusinessActionService` | PendingAction 权威状态机与 Memory 终态收口 |
| Java | `JdbcAiTaskMemoryRepository` | 复合 key 隔离与原子状态机 SQL |

已删除 Python→Java Memory 反向 HTTP 客户端、HMAC write scope 和内部 Memory Write
Endpoint。`JAVA_BASE_URL / JAVA_INTERNAL_TOKEN` 仅服务于 Python 的企业只读 Tool，
不再是 Memory 写入前置条件。

## 7. 配置与失败语义

| 配置 | 默认值 | 行为 |
| --- | --- | --- |
| `MEMORY_WRITE_MODE` | `DISABLED` | `DISABLED` / `AUDIT_ONLY` / `ENABLED` |
| `MEMORY_EXTRACTOR_MAX_INPUT_CHARS` | `12000` | Extractor 输入预算 |

失败语义：

- Extractor 非法 JSON/schema：无提案，主响应继续；
- Pipeline/Dispatcher 异常：记录无敏感字段审计，主响应继续；
- Java Memory 校验、状态冲突或数据库异常：不写入，主响应继续；
- PendingAction 创建失败：不处理本次 Memory 提案；
- `MEMORY_WRITE_MODE=DISABLED`：零额外 Extractor 成本。

## 8. 明确不做

- 跨会话长期画像、偏好记忆、向量记忆；
- 用 Memory 决定权限、owner、employeeId 或业务事实；
- Python/LLM 决定 Memory 终态；
- 自动 retry 或静默 fallback；
- 用 Checkpointer 替代业务数据库状态机。
