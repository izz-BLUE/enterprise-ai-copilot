# 作用域会话记忆验收

这份文档是 Memory 当前实现的验收摘要。它不把 Memory 描述成用户画像、偏好库、向量记忆或业务事实表。

## 验收清单

### 作用域与权威

- [x] Java 使用当前 `VerifiedIdentity.userId()` 和解析后的 `conversationId` 组成唯一 scope。
- [x] Python/LLM/前端不能指定 Memory owner。
- [x] Java PostgreSQL 是 Memory lifecycle、ACTIVE read 和 terminal transition authority。
- [x] `ExpenseStatus`、`BusinessAction` 与 Memory status 保持独立。

### 读取路径

- [x] 只读取 `ACTIVE` Memory。
- [x] `memoryContext` 作为不可信 context 传给 Python。
- [x] 无 Memory、终态 Memory 或 task type 不匹配时不 hydrate `execution_history`。
- [x] `execution_history` 有界、脱敏并标记 `CONTEXT_ONLY`。

### 触发与写入路径

- [x] `action_proposal` 和白名单 Memory-eligible Tool success 才能触发 Extractor。
- [x] 现有 ACTIVE Memory 不会单独触发。
- [x] Pure RAG、eval、read-only business tools、失败和拒答不触发。
- [x] Python 只返回 `UPSERT + ACTIVE` proposal。
- [x] Python terminal write 被策略阻断；Java 负责完成/放弃/终态。
- [x] Action proposal 先建立 Java PendingAction，再写 Memory。

### 恢复与失败行为

- [x] HITL/external resume 不运行 Memory proposal pipeline。
- [x] Checkpoint/recovery 不把 Memory 当作权限或业务事实。
- [x] revalidation stale（重新校验过期）→ Action FAILED + Memory ABANDONED + HITL REJECTED。
- [x] infrastructure failure 不伪造 Memory terminal 或 Graph terminal。
- [x] Java/Python failure semantics 可重试且不泄露 secret、nonce digest 或异常细节。

## 实现映射

| 关注点 | 权威实现 |
|---|---|
| Java 读写权威 | `backend-java/src/main/java/com/fantuan/copilot/controller/LangGraphAgentController.java`、`backend-java/src/main/java/com/fantuan/copilot/service/memory/AiTaskMemoryService.java` |
| Python 触发 | `agent-python/app/memory/memory_trigger_policy.py` |
| Python 流水线 | `agent-python/app/memory/memory_pipeline.py` |
| Python 写入策略 | `agent-python/app/memory/memory_write_policy.py` |
| capability allowlist | `agent-python/app/capabilities/memory_capability_registry.py` 和 `agent-python/app/capabilities/expense_capability.py` |
| Agent 上下文 | `agent-python/app/agents/runtime_context.py` |
| checkpoint 运行时 | `agent-python/app/runtime/checkpoint_runtime.py` |

## 验证基线

Memory 相关行为包含在当前 Python full suite 与 Java integration baseline 中；PostgreSQL checkpoint/recovery/HITL/external resume 集成测试不进入当前正式业务 Domain 之外的 Proof 生命周期。这些数字是项目接受基线，不把本页变成一次新的生产容量声明。

## 非目标

- 不实现 Profile Memory、Preference Memory、Vector Memory 或跨用户共享 Memory；
- 不让 Memory 直接触发业务动作或绕过 PendingAction；
- 不让 Python 写 Java terminal Memory lifecycle；
- 不让 execution history 取代当前 MCP/Java revalidation；
- 不把 Checkpoint 当作业务数据库、权限数据库或审计系统。

详细运行时链路见 [memory-architecture.md](memory-architecture.md)，安全边界见 [memory-security.md](memory-security.md)。
