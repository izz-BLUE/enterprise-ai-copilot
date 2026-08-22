# Scoped Conversation Memory P0 Change Log

本文件是 Memory P0 Phase 1~6 的最终收口记录。时间线按代码 / 测试 / 文档中的
Phase 标记整理；当前工作区的 Memory 实现尚未形成按阶段拆分的 commit，因此这里
不虚构日历日期或 commit hash。

## 1. Phase 1~6 时间线

| 阶段 | 目标 | 主要结果 / 证据 |
| --- | --- | --- |
| Phase 1 — Contract | 定义 MemoryProposal 与提取输入的封闭数据契约 | `agent-python/app/schemas/memory_schema.py`；Pydantic `extra='forbid'`；action / task_type / status / task_state / summary 白名单 |
| Phase 2 — Read Path + 初始写入安全边界 | 让 Java 只把当前用户当前会话的 ACTIVE memory 注入 Python，并建立 task state / summary 的 Python 侧清洗边界 | `main.py` / `langgraph_agent.py` 的 `memoryContext` 注入；`memory_write_policy.py`；Java `InternalAgentChatRequest` 与 `AiTaskMemoryService` |
| Phase 3 — Write Pipeline | 建立 Trigger → Extractor → Pipeline → WritePolicy → Dispatcher 的确定性链路 | `memory_trigger_policy.py`、`memory_extractor.py`、`memory_llm_adapter.py`、`memory_pipeline.py`、`memory_write_dispatcher.py`；解析失败与调度失败分层 |
| Phase 4 — Java Endpoint + Runtime Hook | 通过 Java 权威持久化、短时 scope 和出口旁路接入写入；默认不写 | `java_memory_client.py`；Java memory DTO / Controller / Service / Repository；`memory_runtime_hook.py`、`memory_audit.py`、`memory_write_mode.py` |
| Phase 5 — Evaluation Governance | 在不调用真实 Java / DB / Runtime 的前提下建立评估、指标、成本、配额、灰度和 Release Gate | `eval/memory/` 下的 Case、YAML loader、Evaluator、Metrics / Cost / Release 评估；`memory_quota_policy.py`、`memory_rollout_policy.py`；Release Gate 默认 fail-closed |
| Phase 6 — Freeze | 固定 Read / Write / Control / Security / Failure 五条边界，补齐依赖审计和测试索引 | `docs/memory-p0-architecture.md`；`agent-python/tests/memory/README.md`；`test_memory_dependency_boundary.py`；本文件及配套最终审计文档 |

## 2. 新增文件列表

> **归档状态（本文档更新时点）**：v1 的治理 / 评估组件（`memory_metrics.py`、
> `memory_quota_policy.py`、`memory_rollout_policy.py`、`memory_candidate.py`、
> `memory_task_resolution_policy.py` 与 `eval/memory/` 全套）已整体归档至
> `archive/memory-v1/`，不在运行时导入路径；当前活跃的 Memory 运行链路
> （Read Path + Write Pipeline + Java Endpoint + Runtime Hook）以
> `docs/memory-runtime-v1-architecture.md` 为准。

### Python Schema / Runtime（活跃）

- `agent-python/app/schemas/memory_schema.py`
- `agent-python/app/clients/java_memory_client.py`
- `agent-python/app/memory/__init__.py`
- `agent-python/app/memory/memory_audit.py`
- `agent-python/app/memory/memory_extractor.py`
- `agent-python/app/memory/memory_llm_adapter.py`
- `agent-python/app/memory/memory_pipeline.py`
- `agent-python/app/memory/memory_runtime_hook.py`
- `agent-python/app/memory/memory_task_type_policy.py`
- `agent-python/app/memory/memory_trigger_policy.py`
- `agent-python/app/memory/memory_write_dispatcher.py`
- `agent-python/app/memory/memory_write_mode.py`
- `agent-python/app/memory/memory_write_policy.py`
- `agent-python/app/capabilities/memory_capability.py`
- `agent-python/app/capabilities/memory_capability_registry.py`
- `agent-python/app/capabilities/p0_default_capabilities.py`

### Python v1 治理 / 评估（已归档至 `archive/memory-v1/`）

- `archive/memory-v1/agent-python/app/memory/memory_candidate.py`
- `archive/memory-v1/agent-python/app/memory/memory_metrics.py`
- `archive/memory-v1/agent-python/app/memory/memory_quota_policy.py`
- `archive/memory-v1/agent-python/app/memory/memory_rollout_policy.py`
- `archive/memory-v1/agent-python/app/memory/memory_task_resolution_policy.py`
- `archive/memory-v1/agent-python/eval/memory/`（Case schema / loader / evaluator /
  cost / release audit 及 `cases/*.yaml`）

### Java Memory Boundary

- `backend-java/src/main/java/com/fantuan/copilot/dto/InternalAgentChatRequest.java`
- `backend-java/src/main/java/com/fantuan/copilot/dto/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/model/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/repository/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/service/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/controller/memory/*.java`
- `backend-java/src/main/resources/db/migration/V3__create_ai_task_memory.sql`
- `backend-java/src/main/resources/application-local.yml`（本地 Postgres 开发配置）

### Tests / Governance Documentation

- `agent-python/tests/test_memory_*.py`
- `agent-python/tests/memory/README.md`
- `docs/memory-p0-architecture.md`
- `docs/memory-runtime-v1-architecture.md`（运行时实际子集，以代码为准）

上面的 glob 仅用于表示当前 Memory P0 文件集合；提交前应使用 `git diff --name-only`
逐个核对 staging 内容，避免把无关工作区改动带入 Memory commit。

## 3. 核心设计决策

1. Memory 保存的是跨请求任务连续性，不是用户画像、权限缓存或业务动作授权。
2. Java 是最终 owner / authorization authority；Python 只负责观察、提取、清洗和透传。
3. Read Path 只读取 `(trusted user_id, conversation_id)` 对应的 `ACTIVE` 记录；
   `COMPLETED` / `ABANDONED` 不回注 Planner。
4. `memoryContext` 是不可信历史数据，不得扩大 Tool 能力、覆盖 trusted 字段或进入
   Tool arguments。
5. Write Path 通过 Schema、Python Policy、Java Service 三层防御过滤 trusted 字段和大小。
6. `DISABLED` 是默认模式；`AUDIT_ONLY` 只观察；`ENABLED` 还必须具备 Java 签发的
   conversation-bound scope。
7. Runtime 错误 fail-safe，不阻断主 Agent 响应；Release Gate 对持续错误和缺失证据
   fail-closed，不自动替运维做启用决定。
8. Evaluation / Metrics / Cost / Release 组件是离线或旁路控制面，不反向修改 Runtime。

## 4. Security Boundary

| 边界 | 最终约束 |
| --- | --- |
| `user_id` | 来自 Java `VerifiedIdentity`；写入时只从 Java 签发并验签的 scope 得到，Python body / LLM / 前端均不能提供 |
| `conversation_id` | Java 服务端校验并生成 / 接受分组 hint，写入 path 必须与 scope 绑定值一致；只是 namespace，不承担授权 |
| `employee_id` / `business_date` / 权限 | Java / 受控请求上下文注入，不进入 MemoryProposal、MemoryWriteCommand 或 LLM arguments |
| `task_state` / `taskState` | Python 递归过滤 + 脱敏，Java Service 再次过滤和大小校验 |
| scope | `X-Memory-Write-Scope` 为 Java HMAC 签发的短时 opaque scope，绑定 user 与 conversation；Python 只透传 |
| isolation | 数据库按 `(user_id, conversation_id)` 复合 key 隔离；Read Path 只返回 ACTIVE；跨用户同 conversation 测试覆盖 |

## 5. Failure Boundary

- Extractor 输出非法 JSON / schema 时，`MemoryExtractionParseError` 降级为无 proposal，
  不阻断 Agent。
- Pipeline / Dispatcher / Java Client 的非预期失败保留异常链，Runtime Hook 记录 audit
  后降级主响应；写失败由 metrics / Release Gate 作为上线证据处理。
- Java Read Path 读取异常按“无 Memory”继续；不会把数据库异常转换为伪造上下文。
- Evaluation case、依赖边界或 Release Gate 失败只阻断“建议开启 Memory”，不修改生产开关。

## 6. Final Audit Notes

### 6.1 Contract consistency

| 语义字段 | Python 内部 | Java / HTTP 形态 | 结论 |
| --- | --- | --- | --- |
| task type | `task_type` | 写入 JSON `taskType`；读视图 `taskType` | 通过 `JavaMemoryClient` 显式映射，一致 |
| task state | `task_state: dict` | 写入 JSON `taskState: Map`；读视图 `taskStateJson: String` | 写入与读取是两种明确表示；非同名错误，但需保持文档说明 |
| status | `ACTIVE / COMPLETED / ABANDONED` | Java `TaskStatus` / DTO pattern / DB CHECK 相同 | 一致；Python Proposal 可选，Command 和 Java 写入请求必填 |
| summary | Python ≤ 500 chars，默认空串 | Java DTO ≤ 500，Service / DB 同步限制 | 一致 |
| action | Proposal 允许 `NONE / UPSERT / COMPLETE / ABANDON` | Write Command / Java endpoint 只接受后三者 | 通过 WritePolicy 丢弃 `NONE` 后一致；不允许绕过 Policy 直发 NONE |

审计保留两点契约注意事项，不在本 Phase 自动修改：

- Java `InternalMemoryWriteRequest` 使用 `@JsonIgnoreProperties(ignoreUnknown = true)`，
  因此 Java DTO 自身不是顶层 extra-forbid；当前 Python outbound 白名单和 Java Service
  的字段 / trusted 校验仍构成有效边界。
- Python `MemoryProposal.task_type` 是 P0 受控 Literal，Java `taskType` 是长度受限字符串；
  Java 接受面更宽，但正常 Python Write Path 不会生成枚举外值。

### 6.2 Config consistency

| 配置 | default | example | documentation | 结论 |
| --- | --- | --- | --- | --- |
| `MEMORY_WRITE_MODE` | Python `DISABLED` | `agent-python/.env.example` 明确写 `DISABLED` | README / `docs/api.md` / `docs/deployment.md` | 一致，默认关闭 |
| `JAVA_BASE_URL` | Python 空串；缺失时不创建 writer | `.env.example` 注释示例 `http://localhost:8080` | README / deployment 说明 compose 默认不注入 | 一致，属于 Python → Java 地址 |
| `JAVA_INTERNAL_TOKEN` | Python 与 Java property 均为空串 | `.env.example` 留空 | README / API / deployment 说明生产注入且不能入 Git | 一致，空值关闭内部链路 |
| rollout | `MemoryRolloutPolicy(enabled=False, percentage=0)` | 没有环境变量示例 | 没有正式 env / deployment 配置项；仅架构文档描述策略 | **文档 / 配置接入缺口**；本轮只记录，不自动接入 Runtime |

## 7. Scope of this freeze

本次 Final Audit 只新增审计文档，不修改 LangGraph、PlannerDecision、AgentState、
Memory Runtime、Java endpoint contract 或 frontend；不执行 commit、push、deploy。

## 8. 审计修复记录（audit-driven hardening）

对分支 `feat/scoped-conversation-memory-p0` 的 NOT_READY 审计结论，落地四项修复：

1. **状态机由 Java 原子 SQL 强制**：`JdbcAiTaskMemoryRepository.upsert` 改为
   单条条件语句（无记录仅允许写 ACTIVE；已有记录按白名单条件覆盖），
   `transitionToTerminal` 提供终态收口；非法转换（无记录写终态 / 终态重新激活 /
   终态互转）返回 409 `MEMORY_STATE_CONFLICT` 且不落库。文档白名单补充
   `(ACTIVE, COMPLETE)` / `(ACTIVE, ABANDON)` 两项（归档实现遗漏的业务必需转换）。
   测试：`AiTaskMemoryStateMachineIntegrationTest`（20 例）。
2. **Memory 生命周期由 Java 收口**：`business_action` 增加
   `owner_user_id + conversation_id`（V4 migration）；`BusinessActionService`
   在确认成功 → COMPLETED、取消 / 过期 / 创建失败 / 处理失败 → ABANDONED，
   与 PendingAction 状态变更同一事务；`LangGraphAgentController` 在
   PendingAction 创建失败时同步收口。测试：`BusinessActionPersistenceIntegrationTest`
   新增 7 例生命周期用例。
3. **脱敏长度绕过修复**：Python `memory_write_policy` 删除 4096 字符短路，
   任意长度完整扫描；Java `AiTaskMemoryService` 增加独立内容安全边界
   （结构化路径替换 `[REDACTED]`，JSON 字符串路径拒绝）。
   测试：Python 超长反例 4 例 + Java 脱敏 3 例。
4. **Trigger 失败终态短路**：`route=error` 或 `stop_reason ∈
   {provider_error, invalid_decision, step_budget_exhausted}` 时不进入
   Extractor（reason=`agent_failure_terminal`）。测试：
   `TestAgentFailureShortCircuit` 6 例。
5. **同会话活动 Action 唯一性（P1）**：`ai_task_memory` 以
   `(user_id, conversation_id)` 为唯一键，但 `createPending` 此前允许同会话
   多个活动 PendingAction——任一动作进入终态都会收口整条会话 Memory，
   误伤其他待确认动作的续接。修复：`PendingActionRepository` 新增
   `hasActiveByOwnerAndConversation`，`createPending` 在控制锁内拒绝同会话
   第二个活动动作（409 `ACTION_CONVERSATION_IN_PROGRESS`），
   `LangGraphAgentController` 对该错误码返回可操作提示；
   `conversationId` 为 null（无 Memory 关联）不限制。测试：
   `BusinessActionPersistenceIntegrationTest` 新增 5 例（拒绝 / 取消后可再建 /
   过期后可再建 / 会话隔离 / 并发至多一个）+ 单测 2 例 + Controller 1 例。
