# Memory P0 Commit Preparation Plan

本计划只规定后续如何拆分和验收 commit，不执行 commit、push 或 deploy。
当前工作区已有大量未提交改动，提交前必须按路径精确 staging，不能使用宽泛的
`git add -A` 把无关 Auth / Agent / Frontend 改动混入 Memory P0。

## Recommended commit order

### 1. Read Path

范围：

- Java `InternalAgentChatRequest` / Memory read DTO；
- Java `LangGraphAgentController` 的 verified identity、conversation namespace、ACTIVE 查询；
- Python `ChatRequest.memoryContext` 与只读注入适配；
- 对应 Read Path / isolation tests。

验收重点：

- `user_id` 只来自 `VerifiedIdentity`；
- 只读 ACTIVE；
- `memoryContext` 不暴露给 frontend，不改变 Tool capability；
- 读库失败仍走无 Memory 主链路。

### 2. Write Pipeline

范围：

- `MemoryProposal` / `MemoryExtractionInput`；
- Trigger、Extractor、LLM adapter、Pipeline、WritePolicy、Quota；
- Python unit tests。

验收重点：

- 顶层 schema extra-forbid；
- task state trusted-key 递归过滤、脱敏、16 KiB / 500 字符边界；
- NONE 不产生写 Command；
- action / status 状态机白名单完整。

### 3. Java Endpoint

范围：

- Java Memory Request / Response DTO；
- Controller、Exception Handler、Service、Repository、TaskStatus；
- Flyway `V3__create_ai_task_memory.sql`；
- Java endpoint / service / isolation tests。

验收重点：

- body 不接受 owner 字段；
- internal token + signed scope 双重校验；
- path conversationId 与 scope 一致；
- Java 侧再次过滤 trusted taskState 并执行最终状态 / DB 校验。

### 4. Runtime Hook

范围：

- `JavaMemoryClient` 与 Dispatcher writer 适配；
- `MemoryRuntimeHook` 出口旁路；
- `MemoryWriteMode`、Audit / Metrics 接入；
- Runtime integration tests。

验收重点：

- `DISABLED` 默认无 Extractor / 无写入；
- `AUDIT_ONLY` 只观察；
- `ENABLED` 缺 Java 配置 / scope 时 fail-closed；
- Pipeline / Java / Dispatcher 错误不阻断主响应；
- 当前路径不做 retry / fallback。

### 5. Evaluation Governance

范围：

- `eval/memory/` Case schema、YAML loader、Memory Evaluator；
- Metrics、Cost、Quota、Rollout、Release Gate；
- evaluation / dependency boundary / release tests；
- 离线 cases 和测试索引。

验收重点：

- 不调用真实 DB、Java、LLM 或 Runtime；
- Case / Result extra-forbid；
- prompt injection、NONE、false positive、isolation、determinism 覆盖；
- Release Gate fail-closed，READY 不自动写回 Runtime 开关；
- error taxonomy 能区分 expected parse 与 write / pipeline failure。

### 6. Documentation

范围：

- `docs/memory-p0-architecture.md`；
- `docs/memory-p0-change-log.md`；
- `docs/memory-error-taxonomy.md`；
- `docs/memory-security-checklist.md`；
- `docs/memory-p0-commit-plan.md`；
- `agent-python/tests/memory/README.md`。

验收重点：

- docs 与代码、DTO、配置、测试一致；
- 明确 `taskState`（write Map）与 `taskStateJson`（read view string）的表示差异；
- 不写入真实 token、密码、scope 或完整业务 payload；
- 公网状态使用“仓库部署默认 / 运维环境决定”的谨慎表述。

## Pre-commit gates

1. `git status --short`：确认所有既有 dirty changes 的归属，逐 commit 选择路径。
2. Contract audit：核对 Python snake_case、Java camelCase、read `taskStateJson` 表示和 action 状态机。
3. Security audit：核对 identity、scope、path binding、trusted filtering、isolation。
4. Python：`uv run pytest`，至少覆盖 `tests/test_memory_*.py` 与 dependency boundary。
5. Java：执行 Memory Controller / Service / Isolation 相关测试，并确认 Flyway / Testcontainers 结果。
6. `git diff --check`、secret scan、`.gitignore` 检查；报告只记录 safe metadata，不输出 token / password / hash。
7. 检查 docs 链接和路径；确认未改 LangGraph、PlannerDecision、AgentState、frontend 或 Java endpoint contract。
8. 只有所有必需检查通过后，才由用户决定是否执行 commit；本轮不执行任何 commit / push / deploy。

## Known items to carry into review

- Java DTO unknown top-level fields 使用 ignore，而 Python outbound 是 whitelist；这是当前防御纵深，
  不是本轮自动修改项。
- `MemoryRolloutPolicy` 有安全默认值，但目前没有环境配置注入 / `.env.example` / deployment 配置项；
  后续若接入，必须独立设计并补 Runtime / rollout tests。
- `MemoryReleaseEvaluator` 对 error 聚合 fail-closed；expected parse noop 与 fatal write / pipeline
  failure 的指标语义必须在发布报告中保持可区分。
