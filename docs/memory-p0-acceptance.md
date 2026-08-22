# Scoped Conversation Memory P0 验收基线

本文件记录 Scoped Conversation Memory P0 在当前分支（`feat/scoped-conversation-memory-p0`）
上的最终审计结论、Pre-commit Gates、归档组件与最近一次验收结果摘要，作为后续
Phase 的回归锚点。

---

## 1. 核心设计决策（基线）

1. Memory 保存的是跨请求任务连续性，不是用户画像、权限缓存或业务动作授权。
2. Java 是最终 owner / authorization authority；Python 只负责观察、提取、清洗和透传。
3. Read Path 只读取 `(trusted user_id, conversation_id)` 对应的 `ACTIVE` 记录；
   `COMPLETED` / `ABANDONED` 不回注 Planner。
4. `memoryContext` 是不可信历史数据，不得扩大 Tool 能力、覆盖 trusted 字段或进入
   Tool arguments。
5. Write Path 通过 Schema、Python Policy、Java Service 三层防御过滤 trusted 字段
   与大小；Java 写入边界同时剥离 task_state 中嵌套的生命周期控制字段
   （status / lifecycle_state / completed / abandoned 等），避免污染 Agent 上下文。
6. Python 写入入口只允许 `UPSERT + ACTIVE`；终态由 Java 业务生命周期收口。
7. `DISABLED` 是默认模式；`AUDIT_ONLY` 只观察；`ENABLED` 还必须具备 Java 签发的
   conversation-bound scope。
8. Runtime 错误 fail-safe，不阻断主 Agent 响应；Release Gate 对持续错误和缺失证据
   fail-closed（已归档至 `archive/memory-v1/`）。
9. Evaluation / Metrics / Cost / Release 组件是离线或旁路控制面，不反向修改 Runtime。

---

## 2. Security Boundary（汇总）

| 边界 | 最终约束 |
| --- | --- |
| `user_id` | 来自 Java `VerifiedIdentity`；写入时只从 Java 签发并验签的 scope 得到，Python body / LLM / 前端均不能提供 |
| `conversation_id` | Java 服务端校验并生成 / 接受分组 hint，写入 path 必须与 scope 绑定值一致；只是 namespace，不承担授权 |
| `employee_id` / `business_date` / 权限 | Java / 受控请求上下文注入，不进入 MemoryProposal、MemoryWriteCommand 或 LLM arguments |
| `task_state` / `taskState` | Python 递归过滤 trusted 键 + 脱敏；Java Service 再次过滤 + 大小校验 + 剥离生命周期控制字段 |
| scope | `X-Memory-Write-Scope` 为 Java HMAC 签发的短时 opaque scope，绑定 user 与 conversation；Python 只透传 |
| isolation | 数据库按 `(user_id, conversation_id)` 复合 key 隔离；Read Path 只返回 ACTIVE；跨用户同 conversation 测试覆盖 |

完整勾选项 / 错误分类详见 [memory-security.md](memory-security.md)。

---

## 3. Failure Boundary（汇总）

- Extractor 输出非法 JSON / schema 时，`MemoryExtractionParseError` 降级为无 proposal，
  不阻断 Agent。
- Pipeline / Dispatcher / Java Client 的非预期失败保留异常链，Runtime Hook 记录 audit
  后降级主响应。
- Java Read Path 读取异常按"无 Memory"继续；不会把数据库异常转换为伪造上下文。
- Evaluation case、依赖边界或 Release Gate 失败只阻断"建议开启 Memory"，不修改生产开关
  （已归档，不在当前运行时路径）。

---

## 4. 归档组件（不在运行时导入路径）

v1 治理 / 评估组件已整体归档至 `archive/memory-v1/`，本 Phase 不再恢复：

- `agent-python/app/memory/memory_candidate.py`
- `agent-python/app/memory/memory_metrics.py`
- `agent-python/app/memory/memory_quota_policy.py`
- `agent-python/app/memory/memory_rollout_policy.py`
- `agent-python/app/memory/memory_task_resolution_policy.py`
- `agent-python/eval/memory/`（Case schema / loader / evaluator / cost /
  release audit 及 `cases/*.yaml`）

后续若重新接入，必须独立设计 Runtime 入口并补 rollout / 灰度 / Release Gate
验证与独立测试。

---

## 5. 当前活跃 Memory 文件清单

### Python Schema / Runtime

- `agent-python/app/schemas/memory_schema.py`
- `agent-python/app/schemas/chat_schema.py`（`MemoryContext` 注入契约）
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

### Java Memory Boundary

- `backend-java/src/main/java/com/fantuan/copilot/dto/InternalAgentChatRequest.java`
- `backend-java/src/main/java/com/fantuan/copilot/dto/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/model/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/repository/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/service/memory/*.java`
- `backend-java/src/main/java/com/fantuan/copilot/controller/memory/*.java`
- `backend-java/src/main/resources/db/migration/V3__create_ai_task_memory.sql`

### Tests / Documentation

- `agent-python/tests/test_memory_*.py`
- `agent-python/tests/memory/README.md`
- `agent-python/tests/test_memory_dependency_boundary.py`
- [memory-architecture.md](memory-architecture.md)
- [memory-security.md](memory-security.md)
- 本文（memory-p0-acceptance.md）

---

## 6. Pre-commit Gates（提交前最小验收）

> 当前工作区未执行 commit / push / PR；下列项是任何后续 Memory P0 commit
> 提交前的最小验证清单，逐项勾选后方可推进。

1. `git status --short`：确认所有 dirty changes 的归属，逐 commit 选择路径。
2. Contract audit：核对 Python snake_case、Java camelCase、read `taskStateJson`
   表示和 action 状态机（详见 `memory-architecture.md` 第 4 节）。
3. Security audit：核对 identity、scope、path binding、trusted filtering、isolation、
   生命周期控制字段剥离、Python 写入入口终态拦截（详见 `memory-security.md`）。
4. Python：`uv run pytest`，至少覆盖 `tests/test_memory_*.py` 与 dependency boundary。
5. Java：执行 Memory Controller / Service / State Machine / Integration 测试，
   并确认 Flyway / Testcontainers 结果。
6. `git diff --check`、secret scan、`.gitignore` 检查；报告只记录 safe metadata，
   不输出 token / password / hash。
7. 检查 docs 链接和路径；确认未改 LangGraph、PlannerDecision、AgentState、
   frontend 或 Java endpoint contract。
8. 只有所有必需检查通过后，才由用户决定是否执行 commit；本轮不执行任何 commit /
   push / deploy。

---

## 7. 已知治理缺口（不消除，仅记录）

- Java DTO unknown top-level fields 使用 `@JsonIgnoreProperties(ignoreUnknown=true)`，
  而 Python outbound 是 whitelist；这是当前防御纵深，不是本轮自动修改项。
- `MemoryRolloutPolicy` 有安全默认值，但目前没有环境配置注入 / `.env.example` /
  deployment 配置项；该组件已归档。
- `MemoryReleaseEvaluator` 对 error 聚合 fail-closed（已归档）；expected parse noop
  与 fatal write / pipeline failure 的指标语义必须在发布报告中保持可区分。

---

## 8. 最近的最终验收结果

最近一次真实链路验收（在 `feat/scoped-conversation-memory-p0` 分支上以 `MEMORY_WRITE_MODE=ENABLED`
运行）的关键结果：

- 嵌套生命周期字段写入：Java 写入边界剥离 `status / lifecycle_state` 等顶层与嵌套键，
  DB 中 `task_state_json` 不含这些字段；同会话后续请求 Planner 拿到的是业务续接
  字段，未被误判为终态。
- 业务链路终态拦截：Python 业务动作链路下 `action=COMPLETE / ABANDON` 不调用 Dispatcher；
  Python 写入入口直接发 COMPLETE / ABANDON 被 Java 返回
  `409 MEMORY_TERMINAL_NOT_ALLOWED`，Memory 保持 `ACTIVE`。
- Java Confirm 路径：`PendingAction → SUCCEEDED` 与 `Memory → COMPLETED` 在同一事务内
  完成；Memory `updated_at` 与 `PendingAction` `completed_at` 时间一致（同事务）。
- 测试结果摘要：
  - Python 核心测试（`test_memory_extractor / write_policy / pipeline / runtime_hook`）：
    155/155 通过；
  - Java `MemoryWriteControllerTest` / `MemoryWriteEndpointIntegrationTest` /
    `AiTaskMemoryStateMachineIntegrationTest`：37/37 通过；
  - `git diff --check`：无冲突标记（仅 LF/CRLF 行尾警告）。

完整脚本、payload 与日志保留在 `tmp/acceptance-*/`（不在 Git 跟踪），验收脚本不再作为
长期文档固化。

---

## 9. P0 Freeze 验收清单（基线）

- [x] Read Path 信任边界明确（Java identity → ACTIVE → memoryContext）
- [x] Write Path 状态机白名单 + 沙箱 + 大小限制
- [x] Security Boundary 三重防御（schema / policy / Java scope）+ 写入边界剥离生命周期控制字段
- [x] Failure Boundary fail-safe vs fail-closed 边界清晰
- [x] 模块依赖审计通过（无 LangGraph / Planner / HTTP / DB 依赖）
- [x] 不修改 LangGraph / PlannerDecision / AgentState / Java Endpoint / Frontend
- [x] 当前默认 `MEMORY_WRITE_MODE=DISABLED`；`ENABLED` 需另具备 Java scope 与观测