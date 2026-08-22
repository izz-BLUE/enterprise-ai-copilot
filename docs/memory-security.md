# Memory P0 Security & Error Boundary

本文档是 Scoped Conversation Memory P0 的安全边界与运行时错误分类。
勾选项表示当前代码 / 测试 / 文档已有对应证据；未勾选项是已知治理缺口，
本 Phase 只记录，不通过删除校验或放宽边界消除。

> **归档说明**：v1 治理组件（`MemoryRolloutPolicy` / `MemoryReleaseEvaluator`
 / `MemoryMetricsCollector`）已整体归档至 `archive/memory-v1/`，不在运行时
 导入路径；本文仅描述当前活跃运行时路径的安全与错误边界。

---

## 1. Identity / Namespace

- [x] `user_id` 的权威来源是 Java `VerifiedIdentity`，不是前端 body、Python body、
      LLM arguments、MemoryProposal 或 MemoryContext。
- [x] Read Path 以 `(trusted user_id, conversation_id)` 复合 key 查询；Java Controller
      不把 userId 编码进 conversationId。
- [x] `conversation_id` 只作为会话 namespace。客户端可以提供分组 hint，但 Java 负责
      合法性校验 / 缺失时生成 UUID；它本身不授予权限。
- [x] Python 从 Java header 透传 `X-Conversation-Id` / conversation path，不自行决定 owner。
- [x] `employee_id`、`business_date`、role、permission、allow flags 不进入 MemoryProposal、
      MemoryWriteCommand 或 Planner 的可控 arguments。

---

## 2. Trusted Field Filtering

- [x] Python `MemoryProposal` 顶层使用 `extra='forbid'`。
- [x] Python `MemoryExtractionInput` 仅从白名单字段构造，不把 trusted runtime signal 交给 Extractor。
- [x] Python `MemoryWritePolicy` 对 `task_state` 递归剥离 camelCase / snake_case trusted keys，
      并对敏感字符串做保守脱敏。
- [x] Python 脱敏**完整扫描，无长度短路**：task_state 允许到 16 KiB，超长字符串同样
      命中 `[REDACTED]`（不允许 `"A"*5000 + " Bearer token"` 式绕过）。
- [x] Python outbound `JavaMemoryClient` 只序列化 `action / taskType / status / taskState / summary`。
- [x] Java `AiTaskMemoryService` 对 taskState 做第二次 trusted-key 检查和大小校验，
      并对字符串值做独立内容脱敏（结构化路径替换 / JSON 字符串路径拒绝）。
- [x] Java 侧不从 `InternalMemoryWriteRequest` body 接收 userId / employeeId / conversationId。
- [x] **写入时轻量清洗生命周期控制字段**：`AiTaskMemoryService` 在
      `sanitizeTaskStateMap` / `scrubValue` 中递归剥离 task_state 顶层与嵌套
      `status / lifecycle_state / lifecycleState / task_status / taskStatus /
      terminal_state / terminalState / completed / abandoned`；剥离是写入边界
      无害化，不替代顶层状态机本身。
- [ ] Java DTO 自身仍使用 `@JsonIgnoreProperties(ignoreUnknown = true)`；顶层
      unknown-field rejection 不是 DTO 层的对称约束。本轮不自动改变契约，只要求
      保留 Python outbound whitelist + Java Service 校验的双重边界。

---

## 3. Scope Token

- [x] `X-Memory-Write-Scope` 由 Java 在已解析 verified identity 后签发。
- [x] scope 使用既有 `JAVA_INTERNAL_TOKEN` HMAC 签名，不新增第二套服务密钥。
- [x] scope 包含并绑定 userId、conversationId、过期时间和随机 nonce；TTL 为短时值（120s）。
- [x] Java endpoint 同时校验 `X-Internal-Token`、scope 签名 / expiry 和 path conversationId 匹配。
- [x] Python 只透传 scope，不解析 claim、不重签、不从 body 生成 scope。
- [x] scope 无效、过期或 path mismatch 返回拒绝，不 fallback 到 body identity。

---

## 4. Read / Write Isolation

- [x] Read Path 只注入 `ACTIVE` memory；`COMPLETED` / `ABANDONED` / 读库异常均不伪造上下文。
- [x] **状态机白名单由 Java 原子 SQL 强制**：无记录仅允许写 ACTIVE；ACTIVE 可 UPSERT /
      COMPLETE / ABANDON；终态仅允许同状态幂等重放；拒绝返回 409 且不落库，
      终态不可能被后写重新激活（并发由 PostgreSQL 行级锁序列化）。
- [x] **Python 写入入口只允许 `UPSERT + ACTIVE`**：`MemoryWriteController` 对
      `request.action ∈ {COMPLETE, ABANDON}` 或 `request.status ∈ {COMPLETED, ABANDONED}`
      返回 `409 MEMORY_TERMINAL_NOT_ALLOWED`，不落库。
- [x] **业务动作链路禁止 Python 侧终态命令**：`MemoryWritePolicy.evaluate(..., allow_terminal_actions=False)`
      直接拒绝 COMPLETE / ABANDON；`MemoryRuntimeHook` 保留防御性拦截（终态命令
      不调 Dispatcher）。LLM 不得猜测任务终态。
- [x] **Memory 生命周期由 Java 收口**：PendingAction 记录 `owner_user_id + conversation_id`；
      确认成功 → COMPLETED；取消 / 过期 / 创建失败 / 处理失败 → ABANDONED；
      与 PendingAction 状态变更同一事务，不依赖 LLM 猜测终态。
- [x] `memoryContext` 不进入公共 `ChatRequest`，前端不能提交或读取该内部字段。
- [x] MemoryContext 作为不可信历史数据，不改变 Tool 可见集合，不进入 Tool arguments。
- [x] 数据库按 `(user_id, conversation_id)` 隔离；相同 conversationId 的不同用户不能互读 / 互写。
- [x] Python / Java 测试覆盖 scope owner、path mismatch、read isolation、trusted key rejection、
      终态拦截、生命周期控制字段剥离。
- [x] `MemoryWriteMode=DISABLED` 默认关闭真实写入；`AUDIT_ONLY` 不调用 Dispatcher。
- [ ] Rollout Policy 的默认值目前只存在于 Python 类（`enabled=False`, `percentage=0`），
      尚未形成环境变量 example / deployment 文档 / Runtime 配置注入。该项是治理接入缺口，
      本 Phase 只记录，不修改 Runtime。
      （注：该 Policy 已随 v1 归档至 `archive/memory-v1/`，缺口记录保留。）

---

## 5. Runtime Safety

- [x] Runtime Hook 对 Pipeline / Java / Dispatcher 失败 fail-safe，不阻断 Agent 主响应。
- [x] 审计与评估结果不复制 userId、employeeId、conversationId、token、nonce 或完整 task payload。
- [x] 本清单不授权开启 `ENABLED`；生产启用必须另有配置、观测和运维确认。

---

## 6. Final disposition

当前 Security Boundary 具备 commit 条件；未勾选项是已知的文档 / 契约治理事项，
不应通过删除校验或放宽边界来消除。它们应在后续专门变更中处理，并单独补测试。

---

## 7. 错误分类（Runtime Error Taxonomy）

### 7.1 分类定义

- **expected failure**：契约内、可预期的拒绝或降级，允许按 fail-safe noop 处理。
- **retryable**：只有底层原因属于暂时性网络 / 服务不可用时才具备重试价值；4xx、schema、
  trusted scope 或状态错误不可盲目重试。
- **release blocker**：发生后不能直接把 Memory 推荐为 ENABLED；需要修复、重跑评估或人工确认。
  （注：当前运行时不做聚合发布判定；本节保留为冻结基线。）

### 7.2 四类核心错误

| Error | 产生位置 / 触发条件 | expected failure | retryable | release blocker | 当前处理 |
| --- | --- | --- | --- | --- | --- |
| `MemoryExtractionParseError` | LLM 输出不是 JSON object、字段非法、extra 字段或 Pydantic 校验失败 | **是**。这是 Extractor 的合法失败信号 | **否**。当前按 noop 处理 | **通常否（单次）** | Pipeline 丢弃 proposal，不调用写入，Runtime 不被阻断 |
| `MemoryPipelineError` | Pipeline 输入契约错误、组件非预期异常或调度 bug | **否** | **否**。先修复或定位根因，不能靠重试掩盖代码错误 | **是** | 保留 cause，由 Runtime Hook 记录并 fail-safe 返回主响应 |
| `MemoryWriteDispatcherError` | Dispatcher 注入的 writer 抛出异常，或 writer 调度失败 | **否**（健康写入路径不应出现） | **条件可重试**：仅当底层原因确认是暂时性服务 / 网络故障；当前 Dispatcher 不自动重试 | **是** | 包装并保留原始异常链，Runtime Hook 记录 `write_success=False` |
| `JavaMemoryClientError` | HTTP client 抛异常，或 Java 返回 HTTP ≥ 400 | **按原因区分**：Java 4xx 是预期拒绝信号 | **条件可重试**：超时、连接失败、5xx 可由外部运维策略评估；400/403 scope、token、payload 不可重试 | **是**（对 ENABLED 写入） | Client 统一包装；不读取业务 response body，不做 retry / fallback |

### 7.3 Java 返回错误的细分

| HTTP / error code | 分类 | 处理建议 |
| --- | --- | --- |
| 400 `MEMORY_PAYLOAD_INVALID` | expected rejection，非 retryable | 检查 Python Command 与 Java DTO / 状态机契约 |
| 400 `MEMORY_TRUSTED_KEY_REJECTED` | expected security rejection，非 retryable | 保持阻断，不放宽过滤；检查输入是否污染 |
| 400 `MEMORY_CONVERSATION_ID_INVALID` | expected rejection，非 retryable | 检查服务端 conversation scope/path，不从客户端扩大信任 |
| 403 `MEMORY_INTERNAL_TOKEN_REQUIRED` | configuration / auth failure，非 retryable | 补齐或轮换服务配置后重新验证 |
| 403 `MEMORY_SCOPE_INVALID` / `MEMORY_SCOPE_MISMATCH` | security rejection，非 retryable | 检查 scope TTL、签名、owner 和 path 绑定 |
| 409 `MEMORY_TERMINAL_NOT_ALLOWED` | expected security rejection，非 retryable | Python 写入入口只允许 UPSERT + ACTIVE；终态由 Java 业务生命周期收口 |
| 409 `MEMORY_STATE_CONFLICT` | expected state-machine rejection，非 retryable | 状态机白名单拒绝（无记录写终态 / 终态重新激活 / 终态互转）；不落库 |
| 500 `MEMORY_INTERNAL_ERROR` | unexpected downstream failure | 需人工定位；只有确认暂时性基础设施故障时才考虑外部重试 |

### 7.4 证据位置

- Python：`agent-python/app/memory/memory_extractor.py`、`memory_pipeline.py`、
  `memory_write_dispatcher.py`、`agent-python/clients/java_memory_client.py`、
  `agent-python/app/memory/memory_runtime_hook.py`、`memory_audit.py`；
- Java：`backend-java/src/main/java/com/fantuan/copilot/service/memory/MemoryWriteException.java`、
  `AiTaskMemoryService.java`、`controller/memory/MemoryWriteController.java`。