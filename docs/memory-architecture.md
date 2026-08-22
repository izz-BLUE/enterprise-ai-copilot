# Scoped Conversation Memory P0 架构说明

本文档是 Scoped Conversation Memory P0 的当前架构基线。它以运行时实际调用
的链路为准，每一条都对应到仓库代码中的具体模块；与之冲突的过期归档
（`MemoryMetricsCollector` / `MemoryReleaseEvaluator` / `MemoryRolloutPolicy`
/ `eval/memory/` 离线评估组件）已整体归档至 `archive/memory-v1/`，不在
当前运行路径，本文不再重复描述。

与 `docs/architecture.md` 的关系：后者描述企业级 RAG + Agent 整体架构；
本文只覆盖 Memory 子系统的运行链路与信任边界。

---

## 1. 业务目标与边界

Memory 的业务目标：

- 让 Agent 在同一 `(user_id, conversation_id)` 下**续接进行中的业务任务**，
  例如"请假申请走到第二步，需要确认日期"；
- 保存的是**跨请求任务连续性**，不是用户画像、权限缓存或业务动作授权；
- `(user_id, conversation_id)` 复合 key 是 Memory 的唯一索引；其他维度
  （task_type / status / 时间）都仅作为元数据。

不在 Memory 的范围（明确不做）：

- 不参与 Tool 能力扩大 / Capability Gate / 权限判断；
- 不进入 Tool arguments，不被 LLM 改写后写回；
- 不替代 PendingAction / Java 业务状态机；终态只能由 Java 业务生命周期收口；
- 不存储身份、权限、token、nonce、idempotency_key 等 trusted 字段。

---

## 2. Read Path

目的：在每个 Agent 请求的入口，把"上一轮 ACTIVE 任务记忆"注入 Planner
只读上下文，使 Planner 能在不修改任何业务授权的前提下恢复任务。

```mermaid
flowchart LR
    UI[Frontend] -->|HTTPS| JC[LangGraphAgentController]
    JC -->|verified identity| IIC[Internal Agent Chat]
    IIC -->|HTTP /agent/langgraph/chat| PY[Python FastAPI]
    PY -->|trusted userId, conversationId, scope| MRH[MemoryRuntimeHook.read]
    MRH -->|read_conversation_memory| JMC[JavaMemoryClient]
    JMC -->|/api/internal/memory/conversations/{id}/active| JSE[Java Memory Endpoint]
    JSE -->|trusted userId + conversationId| REPO[(ai_task_memory)]
    REPO -->|ACTIVE row| JSE
    JSE -->|memoryDto| JMC
    JMC -->|memoryContext dict| MRH
    MRH -->|inbound state.memory_context| PL[Planner]
    PL -->|tool_history context| TE[Tool Executor]
```

关键不变量：

1. `userId` 永远来自 Java `VerifiedIdentity`，Python 不接收、也不允许重新构造；
2. `conversationId` 来自 Java 内部请求 envelope，Python 仅作为 namespace 透传；
3. Read Path **只读取 `status = ACTIVE`** 的记录，`COMPLETED` / `ABANDONED`
   不被读回 Planner；
4. `memoryContext` 是**只读、不被 Planner 信任**的历史数据：
   - 不参与 Tool 决策；
   - 不进入 Tool arguments；
   - 不被改写后写回；
5. Read 库异常 / scope mismatch 一律视作"无 Memory"，不伪造上下文。

实现入口：

- Python：`app/memory/memory_runtime_hook.py`（`after_agent_response` 之前的
  memoryContext 注入）
- Java：`AiTaskMemoryService.findActive(...)` + Internal Endpoint
  `POST /api/internal/memory/conversations/{conversationId}/active`

---

## 3. Write Path

目的：Agent 响应出口旁路做"是否值得跨请求保存"的判断；通过后再
把结构化 `MemoryProposal` 落到 Java 持久层。

```mermaid
flowchart TD
    AR[Agent Result] -->|tool_history, observation, existing_memory| EI[MemoryExtractionInput]
    EI --> MTP[MemoryTriggerPolicy]
    MTP -->|should_extract=True| EXT[MemoryExtractor]
    EXT -->|llm_service.call_llm| MPA[MemoryProposal]
    MPA --> MWP[MemoryWritePolicy]
    MWP -->|trusted key strip + redaction + size guard| MWC[MemoryWriteCommand]
    MWC --> WMD[MemoryWriteMode]
    WMD -->|ENABLED| MWD[MemoryWriteDispatcher]
    WMD -->|DISABLED/AUDIT_ONLY| DROP[drop + audit only]
    MWD --> JMC[JavaMemoryClient]
    JMC -->|/api/internal/memory/conversations/{id}/write| JSE[Java Memory Endpoint]
    JSE -->|scope-bound userId| SVC[AiTaskMemoryService]
    SVC -->|state-machine whitelist + size guard| REPO[(ai_task_memory)]
    JMC -->|ok / error| AUD[MemoryAuditEvent]
    AUD --> REC[MemoryAuditRecorder]
```

关键不变量：

1. `MemoryTriggerPolicy` 是 Pipeline 入口开关：
   - 仅当 `leave_proposal_tool` 成功 / 已有 ACTIVE memory / 明确延续信号
     时才进入 Extractor；
   - **Agent 失败终态不触发**：`route=error` 或 `stop_reason ∈
     {provider_error, invalid_decision, step_budget_exhausted}` 时直接
     短路（reason=`agent_failure_terminal`），即使已有 ACTIVE memory /
     action_proposal —— 失败响应没有可信任务进展，错误诊断走审计通道；
2. `MemoryExtractor` 只接收白名单字段（`question / answer / tool_history /
   observation / existing_memory / action_proposal`），trusted 字段被
   `MemoryExtractionInput.extra='forbid'` 兜底；
3. `MemoryWritePolicy` 是 trusted 字段清洗 + 大小限制的最终边界：
   - `task_state` 内递归剥离 forbidden 键；
   - 字符串值命中敏感关键字 → 替换为 `[REDACTED]`（**完整扫描，无长度
     短路**：task_state 允许到 16 KiB，超长字符串同样必须脱敏）；
   - `task_state` 序列化 ≤ 16 KiB；`summary` ≤ 500 chars；
4. **业务动作链路禁止 Python 侧终态命令**：`action_proposal` 非空或
   `leave_proposal_tool` 成功时，`MemoryWritePolicy.evaluate(..., allow_terminal_actions=False)`
   直接拒绝 `COMPLETE / ABANDON`，避免 LLM 猜测任务终态；`MemoryRuntimeHook`
   也保留一道防御性拦截（终态命令不调 Dispatcher）；
5. **状态机白名单**（Java 侧原子条件 SQL 强制，拒绝时返回 409
   `MEMORY_STATE_CONFLICT`，不落库）：
   - `(None, UPSERT-ACTIVE)` —— 首条创建；
   - `(ACTIVE, UPSERT)` / `(ACTIVE, COMPLETE)` / `(ACTIVE, ABANDON)` —— 续写 / 终结；
   - `(COMPLETED, COMPLETE)` / `(ABANDONED, ABANDON)` —— 幂等重放；
   - 其余组合（无记录直接写终态、终态被 UPSERT 重新激活、终态互转）
     一律拒绝；终态不可能被后写覆盖，并发下由 PostgreSQL 行级锁序列化；
6. **Memory 生命周期由 Java 收口**（不依赖 LLM 猜测）：
   - `PendingAction` 创建时记录 `owner_user_id + conversation_id`
     （与 `ai_task_memory` 复合 key 对齐，V4 migration）；
   - 确认成功 → Memory COMPLETED；取消 / 过期 / 处理失败 / 创建失败 →
     Memory ABANDONED；全部与 PendingAction 状态变更在同一事务内完成；
   - **同会话至多一个活动 PendingAction**：`ai_task_memory` 以
     `(user_id, conversation_id)` 为唯一键、`createPending` 在控制锁内拒绝
     同会话第二个活动动作（409 `ACTION_CONVERSATION_IN_PROGRESS`），
     避免任一动作进入终态时误伤同会话其他待确认动作的续接记忆；
     `conversationId` 为 null 的历史路径（无 Memory 关联）不受限制；
7. **Java 写入时轻量清洗生命周期控制字段**：`AiTaskMemoryService` 在
   `sanitizeTaskStateMap` / `scrubValue` 中递归剥离 task_state 顶层与嵌套
   `status / lifecycle_state / lifecycleState / task_status / taskStatus /
   terminal_state / terminalState / completed / abandoned`；这些键在写入
   边界被剥离，避免污染 Agent 上下文，但**不替代**顶层状态机本身；
8. **Python 写入入口只允许 `UPSERT + ACTIVE`**：`MemoryWriteController` 在
   业务动作链路外仍保留终态拦截 —— `request.action ∈ {COMPLETE, ABANDON}`
   或 `request.status ∈ {COMPLETED, ABANDONED}` 时返回
   `409 MEMORY_TERMINAL_NOT_ALLOWED`，不落库；
9. `MemoryWriteMode` 控制 ENABLED / DISABLED：
   - `DISABLED` 时 Dispatcher 被跳过、仍记录 audit event（写路径不丢信号）；
   - `AUDIT_ONLY` 时跑完 Pipeline 与审计但不调用 Java 写端点；
10. Java 写入只信任 scope 内的 `userId`：
    - `X-Memory-Write-Scope` 由 Java 内部签发，TTL 短（120s）；
    - path 上的 `conversationId` 与 scope 绑定，Java 侧双重校验；
    - Java 侧保留独立内容安全边界：结构化路径对敏感字符串值脱敏，
      JSON 字符串路径命中敏感内容直接拒绝（与 Python 规则对齐）。

实现入口：

- Python：`app/memory/memory_pipeline.py` + `app/memory/memory_write_dispatcher.py`
- Java：`AiTaskMemoryService.upsert / complete / abandon`（状态机原子 SQL）；
  `BusinessActionService.closeMemory`（PendingAction 终态 → Memory 收口）；
  `MemoryWriteController`（终态拦截与 scope 校验）

---

## 4. 运行时模块总览

| 端 | 模块 | 职责 |
| --- | --- | --- |
| Python | `app/schemas/memory_schema.py` | `MemoryProposal`（LLM 记忆写意图契约，`extra='forbid'`）；`MemoryExtractionInput`（Extractor 输入白名单） |
| Python | `app/schemas/chat_schema.py` | `MemoryContext`：Java → Python 只读注入契约 |
| Python | `app/memory/memory_task_type_policy.py` | `MemoryTaskTypePolicy`：task_type 白名单 + tool→task_type 映射 |
| Python | `app/memory/memory_trigger_policy.py` | `MemoryTriggerPolicy`：是否值得调 Extractor 的确定性判定 |
| Python | `app/memory/memory_extractor.py` | `MemoryExtractor`：构造 prompt、解析 LLM 输出为 `MemoryProposal` |
| Python | `app/memory/memory_llm_adapter.py` | `MemoryLLMAdapter`：把现有 `call_llm` 适配为 `(system, user) -> str` |
| Python | `app/memory/memory_write_policy.py` | `MemoryWritePolicy`：清洗 trusted 键、脱敏、大小限制 → `MemoryWriteCommand`；业务动作链路终态拦截 |
| Python | `app/memory/memory_pipeline.py` | `MemoryPipeline`：Trigger → Extractor → WritePolicy 编排 |
| Python | `app/memory/memory_write_mode.py` | `MemoryWriteExecutionPolicy`：DISABLED / AUDIT_ONLY / ENABLED 决策 |
| Python | `app/memory/memory_write_dispatcher.py` | `MemoryWriteDispatcher`：command → writer 分发 |
| Python | `app/clients/java_memory_client.py` | `JavaMemoryClient`：payload 白名单序列化 + HTTP 写入 |
| Python | `app/memory/memory_audit.py` | `MemoryAuditEvent` / `LoggingAuditRecorder`：无敏感字段的审计 |
| Python | `app/memory/memory_runtime_hook.py` | `MemoryRuntimeHook`：出口旁路编排，fail-safe；保留第二道业务动作链路终态拦截 |
| Java | `LangGraphAgentController` | 身份校验、conversationId 解析、读 ACTIVE 记忆、签发 scope、转发 Python |
| Java | `MemoryWriteScopeService` | scope 签发 / 验签（HMAC-SHA256，120 秒 TTL） |
| Java | `MemoryWriteController` | 内部写端点：internal token + scope + path 绑定三重校验；终态拦截 |
| Java | `AiTaskMemoryService` | 写校验：action 状态机、trusted 键剥离、生命周期控制字段剥离、大小限制 |
| Java | `JdbcAiTaskMemoryRepository` | `ai_task_memory` 表 UPSERT（单条条件 SQL） |

---

## 5. 请求链路（请假审批示例）

### 请求 1：发起请假（"我要请年假，下周一和周二"）

1. **前端**：从 sessionStorage 取（或生成）`conversationId`，随
   `POST /api/agent/langgraph/chat` 提交 `{message, conversationId}`。它不是可信身份，
   仅作会话分组 hint。
2. **Java `LangGraphAgentController`**：解析 `VerifiedIdentity` 得到 trusted `userId`；
   校验 conversationId 字符集（缺失/非法则服务端生成 UUID v4）。
3. **读取记忆**：按 `(userId, conversationId)` 复合 key 查 `ai_task_memory`，
   首次请求无记录 → `memoryContext = null`，Planner 走无记忆路径。
4. **签发 scope**：`MemoryWriteScopeService.issue(userId, conversationId)` 生成
   HMAC-SHA256 签名的短时 scope（120 秒 TTL，绑定 userId + conversationId）。
5. **转发 Python**：内部请求体 `{message, memoryContext}`，请求头带
   `X-Conversation-Id`、`X-Memory-Write-Scope`、`X-Employee-Id`、`X-Business-Date` 等。
6. **Python Agent 执行**：Planner 调用 `leave_proposal_tool` 进入受控业务动作链路，
   产出 `action_proposal`（缺日期时为 `missing_fields` 澄清）。
7. **出口旁路**：`MEMORY_WRITE_MODE=ENABLED` 时构造 `MemoryRuntimeHook`，
   调用 `after_agent_response(result, conversation_id)`：
   - **Trigger**：`action_proposal` 非空 → 命中，进入 Extractor；
   - **Extractor**：从 result 白名单提取 6 字段 → 组装 prompt → 经
     `MemoryLLMAdapter` 调 `call_llm` → 严格解析出 `MemoryProposal`；
   - **WritePolicy**：task_type 白名单校验 → 递归剥离 trusted 键 →
     字符串脱敏 → 16 KiB / 500 字符限制 → 输出 `MemoryWriteCommand`；
     `action_proposal` 链路下 `allow_terminal_actions=False` 拦截 COMPLETE / ABANDON；
   - **Mode 决策**：ENABLED → 调 `MemoryWriteDispatcher` → `JavaMemoryClient.write_memory`，
     payload 只含白名单 5 字段（action / taskType / status / taskState / summary），
     POST 到 Java 写端点；
8. **Java 写端点 `MemoryWriteController`**：
   - 校验 `X-Internal-Token` 服务间凭证；
   - 验签 `X-Memory-Write-Scope`（过期/伪造 → 403），**userId 只取自 scope**，
     body 不接受任何身份字段；
   - 强校验 path 上的 conversationId 与 scope 内 conversationId 一致；
   - **终态拦截**：`request.action ∈ {COMPLETE, ABANDON}` 或 `request.status ∈
     {COMPLETED, ABANDONED}` 时返回 `409 MEMORY_TERMINAL_NOT_ALLOWED`，不落库；
9. **`AiTaskMemoryService.writeFromCommand`**：action 状态机（UPSERT 要求显式
   status）→ trusted 键再次递归拒绝 + 敏感内容脱敏 → **剥离生命周期控制字段
   （status / lifecycle_state 等顶层与嵌套）** → JSON 序列化 ≤ 16 KiB →
   `JdbcAiTaskMemoryRepository.upsert` 状态机受限写入（单条原子 SQL：无记录仅允许
   ACTIVE；已有记录按白名单条件覆盖，拒绝时返回 409 `MEMORY_STATE_CONFLICT`，
   不落库）；
10. **审计**：`LoggingAuditRecorder` 记录 `{triggered, proposal_action,
    task_type, write_attempted, write_success, ...}`，不含任何身份/业务字段。
11. **主响应**：全程 Memory 失败都不阻断；用户正常收到 Agent 回答与 action_proposal。

### 请求 2：续接任务（隔天或刷新后，"日期改成周三"）

1. 前端 sessionStorage 保留同一 `conversationId`，再次请求。
2. Java 按 `(userId, conversationId)` 查到 **ACTIVE** 记录 → 构造
   `MemoryContextView(taskType, status, taskStateJson, summary)` 注入内部请求体。
3. Python Planner 在 prompt 末尾渲染 `Memory Context` 块（显式声明为不可信历史
   数据，不得改变 Capability Gate / Tool 权限 / trusted 字段），Planner 据此
   理解"请假任务进行中，等日期确认"。
4. Agent 更新 `action_proposal` → 出口 Trigger 命中（action_proposal + existing_memory
   双信号）→ Extractor 产出新 UPSERT → **覆盖更新**同一行。
5. 任务完成（用户确认 / 审批结束）→ **Java 侧收口**：`BusinessActionService` 在
   PendingAction 进入终态时（同一事务内）把 Memory 置为 `COMPLETED`
   （取消 / 过期 / 创建失败 → `ABANDONED`）→ 后续请求读不到（只读 ACTIVE），
   任务退出续接范围；COMPLETE / ABANDON 不再依赖 LLM 猜测，由 Java
   状态变更权威驱动。

---

## 6. 配置与模式

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `MEMORY_WRITE_MODE` | `DISABLED` | 写入模式：`DISABLED`（不触发 Extractor）/ `AUDIT_ONLY`（只评估不写）/ `ENABLED`（真实写入） |
| `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN` / `JAVA_TIMEOUT_SECONDS` | 空 / 空 / 5 | `JavaMemoryClient` 的写端点地址、服务间凭证、超时 |
| `leave.read.internal-token`（Java） | `${JAVA_INTERNAL_TOKEN:}` | scope 签发密钥与服务间凭证；为空时写端点一律 403 |

当前写入模式：`DISABLED`（仓库部署默认）。生产启用 Memory 必须另外具备：

- `MEMORY_WRITE_MODE=ENABLED`；
- `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN` 已配置且对应服务端正确配置密钥；
- 配套观测 / 运维确认（参见 `docs/memory-p0-acceptance.md` 的 Pre-commit Gates）。

---

## 7. 失败降级（fail-safe，不阻断主响应）

| 场景 | 行为 |
| --- | --- |
| 读记忆失败 / 读库异常 | `memoryContext = null`，Planner 走无记忆路径，仅记日志 |
| Trigger 不命中 | 不调用 Extractor（无额外 LLM 成本） |
| Extractor 输出解析失败 | 跳过写入，audit 记录错误类别 |
| WritePolicy 拒绝（trusted 键 / 超限 / 业务链路终态） | 跳过写入，audit 记录 |
| 状态机拒绝（无记录写终态 / 终态重新激活） | Java 返回 409 `MEMORY_STATE_CONFLICT`，不落库，audit 记录 |
| 终态拦截（Python 写入入口 COMPLETE / ABANDON） | Java 返回 409 `MEMORY_TERMINAL_NOT_ALLOWED`，不落库 |
| Agent 失败终态（route=error / provider_error 等） | Trigger 直接短路（reason=`agent_failure_terminal`），不进入 Extractor |
| `MEMORY_WRITE_MODE=DISABLED` | 请求入口直接短路，零额外成本 |
| `MEMORY_WRITE_MODE=AUDIT_ONLY` | 跑完 Pipeline 并记录审计，不调用 Java 写端点 |
| ENABLED 但缺 scope / Java 配置 | fail-closed writer 抛错 → 落入 audit，主响应不受影响 |
| Java 写端点 4xx / 5xx | 记 audit，主响应不受影响 |
| Audit recorder 自身抛错 | 仅记日志，绝不上抛 |

---

## 8. 模块依赖边界

`app/memory/` 内所有模块在 import 时**禁止**直接依赖：

- `langgraph.*` / `langchain.*`（除 `langchain_core` 的 BaseMessage 等纯类型）
- `app.agents.*`（特别是 `planner_node` / `langgraph_agent`）
- `app.controllers` / `app.routers` / `app.api` / `app.main`
- 数据库驱动（`sqlalchemy` / `asyncpg` / `psycopg`）
- HTTP 客户端（`httpx` / `aiohttp` / `requests`）

允许依赖：

- `pydantic`
- `app.schemas.memory_schema` / `app.schemas.planner_schema`（仅 schema 常量）
- 同包内 `app.memory.*` 模块
- Python 标准库 + 已有项目依赖（`logging`, `hashlib` 等）

本基线由 `tests/test_memory_dependency_boundary.py` 守护。

---

## 9. 归档说明（不重复）

下列组件曾出现在 P0 设计但已整体归档至 `archive/memory-v1/`，不在运行时导入路径：

- `MemoryMetricsCollector` / `MemoryEvaluator` / `MemoryReleaseEvaluator`；
- `MemoryRolloutPolicy` / `MemoryQuotaPolicy`；
- `MemoryCandidate` / `MemoryTaskResolutionPolicy`；
- `eval/memory/`（Case schema / loader / evaluator / cost / release audit / cases yaml）。

后续若重新接入，必须在 Runtime 中补 rollout / 灰度 / Release Gate 验证与独立测试；
本文档不描述其具体行为，避免与代码事实偏离。