# Memory P0 Security & Error Boundary

本文记录 Scoped Conversation Memory 当前安全边界。

## 信任来源

| 字段 | 权威来源 | 非权威来源 |
| --- | --- | --- |
| `user_id` | Java `VerifiedIdentity` | 前端、Python、LLM、Memory 内容 |
| `conversation_id` | Java 当前请求解析结果 | Python 响应、Memory 内容 |
| `employee_id` / 权限 / 业务日期 | Java 认证与配置 | LLM arguments、Memory |
| Memory lifecycle | Java PendingAction 状态机 | Python proposal/action/status |
| task content | Python 提案，Java 校验后保存 | 不可作为业务事实或权限依据 |

## 写入防线

- Python `MemoryProposal`、`MemoryWriteCommand` 和 `AgentMemoryProposal` 都使用严格字段契约。
- Python 递归剥离 trusted/runtime key，敏感字符串整串替换为 `[REDACTED]`。
- Python 对外只允许 `UPSERT + ACTIVE`；`UPSERT + COMPLETED/ABANDONED` 同样被拦截。
- Python 响应不携带 owner、conversationId、action 或 status。
- Java `AiTaskMemoryService` 独立重复检查 trusted key、生命周期控制字段、敏感内容和大小。
- Java summary 与 task-state 字符串都执行大小写不敏感 marker 扫描。
- 数据库以 `(user_id, conversation_id)` 复合 key 隔离，并用条件 SQL 阻止终态重新激活。

## 动作链路时序

带 `action_proposal` 的响应必须先通过 Java 权限与 `createPending`。只有 PendingAction
成功建立后，Java 才处理对应 `memory_proposal`。这阻止失败或重复动作覆盖既有活动
Memory。动作创建失败不执行 Memory 写入，也不错误地终结既有 Memory。

## 已移除的攻击面

当前运行时不再包含：

- Python→Java Memory 反向 HTTP 回调；
- `X-Memory-Write-Scope` HMAC 签发/验签；
- `/api/internal/memory/**/write`；
- Python 可知服务密钥同时充当 owner scope 签名密钥的设计。

`JAVA_INTERNAL_TOKEN` 只用于企业只读 Tool 的内部端点，不参与 Memory owner 或写入。

## Error Boundary

| 位置 | 失败 | 主响应 | 处理 |
| --- | --- | --- | --- |
| Extractor | JSON/schema 非法 | 继续 | 丢弃提案，记录元数据 |
| Pipeline | 非预期异常 | 继续 | `MemoryPipelineError` 保留 cause |
| Dispatcher/response writer | 非 ACTIVE 命令或调度异常 | 继续 | 拦截并审计 |
| Java proposal validation | trusted key、大小、敏感内容问题 | 继续 | 拒绝写入，不记录内容 |
| Java repository | 状态冲突/数据库异常 | 继续 | 不写入，安全日志只记录异常类型 |
| PendingAction creation | 权限/业务/容量/并发冲突 | 返回安全业务错误 | 不处理本次 Memory 提案 |

Memory 是旁路能力，因此 Memory 写入失败不会撤销已经成功创建的 PendingAction。
反过来，Memory 也不能绕过 PendingAction、nonce、幂等确认或最终业务写入边界。

## 回归要求

至少覆盖以下反例：

- `UPSERT + COMPLETED/ABANDONED` 在 Python 被拒绝；
- Python 响应不能提供 owner/conversation/lifecycle；
- 动作创建失败或同会话已有活动动作时不写 Memory；
- 动作成功时 `createPending` 先于 Memory upsert；
- Java 只使用当前 `VerifiedIdentity.userId()`；
- summary/task-state 的 Bearer、JWT、token、password、nonce、idempotency marker 被脱敏；
- 终态 Memory 不能被 Agent 提案重新激活；
- 日志不包含问题、答案、task state、scope、nonce 或业务原因原文。
