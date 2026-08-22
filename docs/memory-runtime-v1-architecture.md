# Memory Runtime v1 简化架构

本文档只描述当前代码库中**真实存在并在运行时被调用的**记忆链路：
读取（Java 入口注入）与写入（Agent 出口旁路），以及两侧的安全边界。
不包含任何未接入运行路径的设计（离线评估、灰度、多候选解析等一律不在本文档范围）。

与 `docs/` 下既有 Memory 架构文档的关系：本文档是"实际运行子集"，以代码为准；
两者冲突时以本文档为准（本文档每一条都能在代码中找到对应调用点）。

---

## 1. 运行时模块总览

| 端 | 模块 | 职责 | 是否运行时调用 |
| --- | --- | --- | --- |
| Python | `app/schemas/memory_schema.py` | `MemoryProposal`（LLM 记忆写意图契约，`extra='forbid'`）；`MemoryExtractionInput`（Extractor 输入白名单） | ✅ |
| Python | `app/schemas/chat_schema.py` | `MemoryContext`：Java → Python 只读注入契约 | ✅ |
| Python | `app/memory/memory_task_type_policy.py` | `MemoryTaskTypePolicy`：task_type 白名单 + tool→task_type 映射 | ✅（仅 `default()` 路径） |
| Python | `app/memory/memory_trigger_policy.py` | `MemoryTriggerPolicy`：是否值得调 Extractor 的确定性判定 | ✅ |
| Python | `app/memory/memory_extractor.py` | `MemoryExtractor`：构造 prompt、解析 LLM 输出为 `MemoryProposal` | ✅ |
| Python | `app/memory/memory_llm_adapter.py` | `MemoryLLMAdapter`：把现有 `call_llm` 适配为 `(system, user) -> str` | ✅ |
| Python | `app/memory/memory_write_policy.py` | `MemoryWritePolicy`：清洗 trusted 键、脱敏、大小限制 → `MemoryWriteCommand` | ✅ |
| Python | `app/memory/memory_pipeline.py` | `MemoryPipeline`：Trigger → Extractor → WritePolicy 编排 | ✅ |
| Python | `app/memory/memory_write_mode.py` | `MemoryWriteExecutionPolicy`：DISABLED / AUDIT_ONLY / ENABLED 决策 | ✅ |
| Python | `app/memory/memory_write_dispatcher.py` | `MemoryWriteDispatcher`：command → writer 分发 | ✅ |
| Python | `app/clients/java_memory_client.py` | `JavaMemoryClient`：payload 白名单序列化 + HTTP 写入 | ✅（ENABLED 时） |
| Python | `app/memory/memory_audit.py` | `MemoryAuditEvent` / `LoggingAuditRecorder`：无敏感字段的审计 | ✅ |
| Python | `app/memory/memory_runtime_hook.py` | `MemoryRuntimeHook`：出口旁路编排，fail-safe | ✅ |
| Python | `app/agents/planner_node.py` | `build_planner_prompt` / `_render_memory_block`：把记忆渲染为不可信历史上下文 | ✅ |
| Java | `LangGraphAgentController` | 身份校验、conversationId 解析、读 ACTIVE 记忆、签发 scope、转发 Python | ✅ |
| Java | `MemoryWriteScopeService` | scope 签发 / 验签（HMAC-SHA256，120 秒 TTL） | ✅ |
| Java | `MemoryWriteController` | 内部写端点：internal token + scope + path 绑定三重校验 | ✅ |
| Java | `AiTaskMemoryService` | 写校验：action 状态机、trusted 键剥离、大小限制 | ✅ |
| Java | `JdbcAiTaskMemoryRepository` | `ai_task_memory` 表 UPSERT | ✅ |

---

## 2. 请求链路

```mermaid
flowchart LR
    FE[前端] -->|POST /api/agent/langgraph/chat<br/>message + conversationId| JC[LangGraphAgentController]
    JC -->|VerifiedIdentity.userId| MEM[读 ai_task_memory<br/>仅 ACTIVE]
    JC -->|issue scope<br/>HMAC + 120s TTL| SCOPE[X-Memory-Write-Scope]
    JC -->|内部请求体 message + memoryContext| PY[Python /agent/langgraph/chat]
    PY -->|memory_context 渲染| PL[Planner prompt]
    PL -->|agent_result| HOOK[MemoryRuntimeHook.after_agent_response]
    HOOK -->|pipeline| PIP[Trigger → Extractor → WritePolicy]
    PIP -->|command| WMD[MemoryWriteDispatcher]
    WMD -->|ENABLED| JMC[JavaMemoryClient]
    JMC -->|POST /api/internal/memory/conversations/{id}/write| MWC[MemoryWriteController]
    MWC -->|scope 验签 + path 绑定| SVC[AiTaskMemoryService]
    SVC -->|UPSERT| REPO[(ai_task_memory)]
```

---

## 3. 完整调用链：请假审批示例

### 请求 1：发起请假（"我要请年假，下周一和周二"）

1. **前端**：从 sessionStorage 取（或生成）`conversationId`，随 `POST /api/agent/langgraph/chat` 提交 `{message, conversationId}`。它不是可信身份，仅作会话分组 hint。
2. **Java `LangGraphAgentController`**：解析 `VerifiedIdentity` 得到 trusted `userId`；校验 conversationId 字符集（缺失/非法则服务端生成 UUID v4）。
3. **读取记忆**：按 `(userId, conversationId)` 复合 key 查 `ai_task_memory`，首次请求无记录 → `memoryContext = null`，Planner 走无记忆路径。
4. **签发 scope**：`MemoryWriteScopeService.issue(userId, conversationId)` 生成 HMAC-SHA256 签名的短时 scope（120 秒 TTL，绑定 userId + conversationId）。
5. **转发 Python**：内部请求体 `{message, memoryContext}`，请求头带 `X-Conversation-Id`、`X-Memory-Write-Scope`、`X-Employee-Id`、`X-Business-Date` 等。
6. **Python Agent 执行**：Planner 调用 `leave_proposal_tool` 进入受控业务动作链路，产出 `action_proposal`（缺日期时为 `missing_fields` 澄清）。
7. **出口旁路**：`MEMORY_WRITE_MODE=ENABLED` 时构造 `MemoryRuntimeHook`，调用 `after_agent_response(result, conversation_id)`：
   - **Trigger**：`action_proposal` 非空 → 命中，进入 Extractor（本次不产生额外 LLM 调用的场景只有纯 RAG 问答）；
   - **Extractor**：从 result 白名单提取 `question / answer / tool_history / observation / action_proposal` → 组装 prompt → 经 `MemoryLLMAdapter` 调 `call_llm` → 严格解析出 `MemoryProposal`，例如 `{action: UPSERT, task_type: LEAVE_REQUEST, status: ACTIVE, task_state: {waiting_for: "date", current_step: "proposal"}, summary: "用户申请年假，等待确认日期"}`；
   - **WritePolicy**：task_type 白名单校验（`LEAVE_REQUEST` 合法）→ 递归剥离 trusted 键 → 字符串脱敏 → 16 KiB / 500 字符限制 → 输出 `MemoryWriteCommand`；
   - **Mode 决策**：ENABLED → 调 `MemoryWriteDispatcher` → `JavaMemoryClient.write_memory`，payload 只含白名单 5 字段（action / taskType / status / taskState / summary），POST 到 Java 写端点；
8. **Java 写端点 `MemoryWriteController`**：
   - 校验 `X-Internal-Token` 服务间凭证；
   - 验签 `X-Memory-Write-Scope`（过期/伪造 → 403），**userId 只取自 scope**，body 不接受任何身份字段；
   - 强校验 path 上的 conversationId 与 scope 内 conversationId 一致；
9. **`AiTaskMemoryService.writeFromCommand`**：action 状态机（UPSERT 要求显式 status）→ trusted 键再次递归拒绝 → JSON 序列化 ≤ 16 KiB → `JdbcAiTaskMemoryRepository.upsert`（`ON CONFLICT (user_id, conversation_id) DO UPDATE`）落库 **ACTIVE**。
10. **审计**：`LoggingAuditRecorder` 记录 `{triggered, proposal_action, task_type, write_attempted, write_success, ...}`，不含任何身份/业务字段。
11. **主响应**：全程 Memory 失败都不阻断；用户正常收到 Agent 回答与 action_proposal。

### 请求 2：续接任务（隔天或刷新后，"日期改成周三"）

1. 前端 sessionStorage 保留同一 `conversationId`，再次请求。
2. Java 按 `(userId, conversationId)` 查到 **ACTIVE** 记录 → 构造 `MemoryContextView(taskType, status, taskStateJson, summary)` 注入内部请求体。
3. Python Planner 在 prompt 末尾渲染 `Memory Context` 块（显式声明为不可信历史数据，不得改变 Capability Gate / Tool 权限 / trusted 字段），Planner 据此理解"请假任务进行中，等日期确认"。
4. Agent 更新 `action_proposal` → 出口 Trigger 命中（action_proposal + existing_memory 双信号）→ Extractor 产出新 UPSERT → **覆盖更新**同一行。
5. 任务完成（用户确认 / 审批结束）→ `action=COMPLETE, status=COMPLETED` → 后续请求读不到（只读 ACTIVE），任务退出续接范围。

---

## 4. 安全边界（运行时实际生效的三重防御）

| 层 | 位置 | 行为 |
| --- | --- | --- |
| 契约层 | Python `MemoryProposal` / `MemoryExtractionInput` | `extra='forbid'`：LLM 输出任何未声明字段直接校验失败 |
| 清洗层 | Python `MemoryWritePolicy` | 递归剥离 `user_id / employee_id / conversation_id / role / permission / token / nonce / idempotency_key` 等 trusted 键；命中敏感关键字的值替换为 `[REDACTED]` |
| 落库层 | Java `AiTaskMemoryService` | 不信任 Python 过滤结果，再次递归拒绝 trusted 键；校验 action/status 状态机与大小上限（DB CHECK 兜底） |

身份边界：

- `userId` 只存在于 Java：来自 `VerifiedIdentity`，写入时从 scope 解析，Python 全程不接触、不生成、不改写；
- scope 由 Java 基于 verified identity 签发，HMAC-SHA256 签名、120 秒 TTL、绑定 `(userId, conversationId)`，path 与 scope 双重校验；
- `conversationId` 仅作命名空间，客户端可提供但服务端权威解析，不参与权限判定；
- 写入 payload 白名单 5 字段，额外字段在序列化阶段剔除。

---

## 5. 失败降级（fail-safe，不阻断主响应）

| 场景 | 行为 |
| --- | --- |
| 读记忆失败 / 读库异常 | `memoryContext = null`，Planner 走无记忆路径，仅记日志 |
| Trigger 不命中 | 不调用 Extractor（无额外 LLM 成本） |
| Extractor 输出解析失败 | 跳过写入，audit 记录错误类别 |
| WritePolicy 拒绝（trusted 键 / 超限） | 跳过写入，audit 记录 |
| `MEMORY_WRITE_MODE=DISABLED` | 请求入口直接短路，零额外成本 |
| `MEMORY_WRITE_MODE=AUDIT_ONLY` | 跑完 Pipeline 并记录审计，不调用 Java 写端点 |
| ENABLED 但缺 scope / Java 配置 | fail-closed writer 抛错 → 落入 audit，主响应不受影响 |
| Java 写端点 4xx / 5xx | 记 audit，主响应不受影响 |
| Audit recorder 自身抛错 | 仅记日志，绝不上抛 |

---

## 6. 配置

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `MEMORY_WRITE_MODE` | `DISABLED` | 写入模式：`DISABLED`（不触发 Extractor）/ `AUDIT_ONLY`（只评估不写）/ `ENABLED`（真实写入） |
| `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN` / `JAVA_TIMEOUT_SECONDS` | 空 / 空 / 5 | JavaMemoryClient 的写端点地址、服务间凭证、超时 |
| `leave.read.internal-token`（Java） | `${JAVA_INTERNAL_TOKEN:}` | scope 签发密钥与服务间凭证；为空时写端点一律 403 |
