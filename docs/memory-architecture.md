# 作用域会话记忆架构

Memory 在本项目中的准确含义是 **Conversation Scoped Task State Persistence**：保存同一用户、同一 conversation 中的当前任务连续性。它不是 Profile/Preference/Vector Memory，也不是业务事实、权限缓存或自动化触发器。

## 1. 作用域与权威

Memory key 是：

```text
(VerifiedIdentity.userId(), resolved conversationId)
```

`user_id` 只来自 Java 当前认证上下文；前端 body、Python、LLM、Tool arguments 和 Memory 内容都不能指定 owner。`conversationId` 只是 Java 校验后的 namespace；缺失时 Java 生成新的 scope。

Java PostgreSQL `ai_task_memory` 是 Memory lifecycle 的唯一 authority。Python 只返回非权威提案；Java 只在当前认证请求中决定 owner、scope、生命周期和持久化。

## 2. 读取路径

```text
React conversationId hint
  → Java resolves identity + conversation scope
  → read ai_task_memory WHERE status = ACTIVE
  → body.memoryContext → Python Runtime Context
  → Planner receives untrusted task context
```

读取路径只注入 `ACTIVE` Memory；`COMPLETED`/`ABANDONED` 不进入新的 Planner context。Memory 内容按不可信历史处理，不能覆盖 `employee_id`、`business_date`、`trace_id`、权限或 Tool capability。

## 3. 触发与写入路径

```text
Agent result
  → MemoryTriggerPolicy
  → Extractor
  → WritePolicy
  → Python response memory_proposal
  → Java authenticated persistence
```

触发规则是显式白名单：

| 结果 | 是否触发 Extractor |
|---|---:|
| `action_proposal` | 是 |
| Memory-eligible Tool 成功（当前如受控 Proposal） | 是 |
| 纯 `rag_answer_tool` | 否 |
| eval、余额、leave request、expense status、travel/invoice read 成功 | 否 |
| Safety refusal、Tool error、LLM/provider error、预算耗尽 | 否 |
| 已存在的 ACTIVE Memory | **否；不能单独触发** |

Python write policy 只允许 `UPSERT + ACTIVE` 的提案；Python 不提交 `COMPLETE`、`ABANDONED` 或其他 terminal Memory state，不直接访问 Java DB。`MEMORY_WRITE_MODE`：

- `DISABLED`：默认关闭，不调用 Extractor；
- `AUDIT_ONLY`：生成提案并记录元数据，不落库；
- `ENABLED`：返回 `UPSERT + ACTIVE` 提案，由 Java 在当前认证上下文落库。

如果有 action proposal，Java 先成功创建 PendingAction，再持久化 Memory；Java Confirm/Cancel/Expire/Stale/Failure 负责将 Memory 收口。Memory terminal status 与 `ExpenseStatus`、`BusinessAction` status 完全分离。

普通 Agent proposal/upsert 不重新激活 `COMPLETED` / `ABANDONED` Memory。旧 Expense clarification 已进入终态后，只有 Java 在当前响应明确开启新的 Expense reason clarification cycle 时，才能通过显式“新业务周期”入口将同一 `(user_id, conversation_id)` 记录置为 `ACTIVE`，写入 `EXPENSE_REQUEST`、`waiting_for=reason` 和当前新 Q1 的 `original_request`；Q2 不覆盖该 Q1。Multi Task Runtime 的 task1 terminal 后，Java 先按既有 terminal authority 收口 task1 Memory；只有 Java Task Runtime 推进 task2 时，才可通过显式的“下一 task 激活”入口把同一记录置为 `ACTIVE` 并写入 task2 proposal。两个显式入口都不放宽普通 Agent proposal 的终态保护，不新增 Memory 表结构，也不改变 TaskExecution 的状态权威。external callback / child execution 不得调用这些入口。

## 4. 状态对比

| 状态 | 作用域/生命周期 | 权威来源 | 明确禁止 |
|---|---|---|---|
| Memory | `(user_id, conversation_id)`；ACTIVE→Java terminal | Java PostgreSQL | 不能充当权限、金额、当前 trip/invoice 事实 |
| `tool_history` | 当前 Agent execution | AgentState | 不能跨请求复用为历史事实；新请求清空 |
| `execution_history` | 有界成功步骤摘要 | LangGraph Checkpoint | 不能做 Tool 去重、ExpenseProposalContext、Memory trigger 或业务查询 |
| LangGraph Checkpoint | runtime thread 的执行现场 | `PostgresSaver` | 不能作为身份、权限或业务数据库 |
| Java business DB | PendingAction、LeaveRequest、ExpenseClaim、Memory lifecycle | Java PostgreSQL | Python/LLM 不能直接写 |

`execution_history` 只在读取到 ACTIVE Memory 且 task type 匹配时 hydrate，所有条目都归一为 `CONTEXT_ONLY`。它可以帮助 Planner 理解已完成的 travel/invoice 步骤，但当前决策仍必须刷新业务事实，不能直接复用历史 `valid`、`duplicate` 或 trip status。

## 5. Checkpoint 关系

Checkpoint 记录 Agent execution state，包括 `tool_history`、bounded `execution_history`、planner counters、execution marker 和 interrupt marker。它支持 crash recovery/HITL/external approval，但不拥有业务授权。

LangGraph 固定使用 `ConnectionPool + PostgresSaver` 持久化 execution snapshot，节点以同步 durability 落盘；`LANGGRAPH_CHECKPOINT_DSN` 缺失或初始化失败即 fail-closed。普通新请求只在 Memory/task gate 通过后 hydrate history；同一次 Resume 保留原 execution state，不重新 hydrate history、不重跑 Planner。

Memory proposal pipeline 不在 HITL resume、external resume 或普通 `WAITING_EXTERNAL` response 中运行。这样避免外部 webhook、resume replay 或单纯业务查询产生意外 Memory trigger。

## 6. 安全不变量

- owner 从 `VerifiedIdentity` 派生，不能从请求体、Memory 或模型输出派生；
- Java 是 Memory lifecycle authority，Python 只能提出 `UPSERT + ACTIVE`；Expense 新 clarification cycle 和 Task Runtime 下一 task 激活都是 Java 控制的显式生命周期操作；
- action proposal 必须先建立 Java PendingAction，不能因 Memory proposal 直接写业务表；
- Memory 不进入 LLM arguments 的 trusted system fields；
- terminal Memory transition 和业务 action result 在 Java 控制下保持可重试、幂等和可审计；
- Python Memory/Extractor/Writer 失败不阻断主 Agent response，但 Java 不会据此伪造成功。

相关安全清单见 [memory-security.md](memory-security.md)，验收记录见 [memory-p0-acceptance.md](memory-p0-acceptance.md)。
