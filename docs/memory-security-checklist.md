# Memory P0 Security Final Checklist

本清单用于后续 commit 前的最终复核。勾选表示当前代码 / 测试 / 文档已有对应证据，
不表示本文件会替代 Java 授权或生产配置检查。

> **归档状态（本文档更新时点）**：末尾"Release Safety"一节引用的
> `MemoryRolloutPolicy` 默认值（`enabled=False, percentage=0`）已随 v1 治理组件
> 归档至 `archive/memory-v1/`；当前运行时没有 rollout 配置注入，未勾选项
> 保持为已知治理缺口。其余勾选项对应当前活跃代码。

## Identity / Namespace

- [x] `user_id` 的权威来源是 Java `VerifiedIdentity`，不是前端 body、Python body、
      LLM arguments、MemoryProposal 或 MemoryContext。
- [x] Read Path 以 `(trusted user_id, conversation_id)` 复合 key 查询；Java Controller
      不把 userId 编码进 conversationId。
- [x] `conversation_id` 只作为会话 namespace。客户端可以提供分组 hint，但 Java 负责
      合法性校验 / 缺失时生成 UUID；它本身不授予权限。
- [x] Python 从 Java header 透传 `X-Conversation-Id` / conversation path，不自行决定 owner。
- [x] `employee_id`、`business_date`、role、permission、allow flags 不进入 MemoryProposal、
      MemoryWriteCommand 或 Planner 的可控 arguments。

## Trusted Field Filtering

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
- [ ] Java DTO 自身仍使用 `@JsonIgnoreProperties(ignoreUnknown = true)`；顶层 unknown-field
      rejection 不是 DTO 层的对称约束。本轮不自动改变契约，只要求 commit review 保留 Python
      outbound whitelist + Java Service 校验的双重边界。

## Scope Token

- [x] `X-Memory-Write-Scope` 由 Java 在已解析 verified identity 后签发。
- [x] scope 使用既有 `JAVA_INTERNAL_TOKEN` HMAC 签名，不新增第二套服务密钥。
- [x] scope 包含并绑定 userId、conversationId、过期时间和随机 nonce；TTL 为短时值。
- [x] Java endpoint 同时校验 `X-Internal-Token`、scope 签名 / expiry 和 path conversationId 匹配。
- [x] Python 只透传 scope，不解析 claim、不重签、不从 body 生成 scope。
- [x] scope 无效、过期或 path mismatch 返回拒绝，不 fallback 到 body identity。

## Read / Write Isolation

- [x] Read Path 只注入 `ACTIVE` memory；`COMPLETED` / `ABANDONED` / 读库异常均不伪造上下文。
- [x] **状态机白名单由 Java 原子 SQL 强制**：无记录仅允许写 ACTIVE；ACTIVE 可 UPSERT /
      COMPLETE / ABANDON；终态仅允许同状态幂等重放；拒绝返回 409 且不落库，
      终态不可能被后写重新激活（并发由 PostgreSQL 行级锁序列化）。
- [x] **Memory 生命周期由 Java 收口**：PendingAction 记录 `owner_user_id + conversation_id`；
      确认成功 → COMPLETED；取消 / 过期 / 创建失败 / 处理失败 → ABANDONED；
      与 PendingAction 状态变更同一事务，不依赖 LLM 猜测终态。
- [x] `memoryContext` 不进入公共 `ChatRequest`，前端不能提交或读取该内部字段。
- [x] MemoryContext 作为不可信历史数据，不改变 Tool 可见集合，不进入 Tool arguments。
- [x] 数据库按 `(user_id, conversation_id)` 隔离；相同 conversationId 的不同用户不能互读 / 互写。
- [x] Python / Java 测试覆盖 scope owner、path mismatch、read isolation、trusted key rejection。
- [x] `MemoryWriteMode=DISABLED` 默认关闭真实写入；`AUDIT_ONLY` 不调用 Dispatcher。
- [ ] Rollout Policy 的默认值目前只存在于 Python 类（`enabled=False`, `percentage=0`），
      尚未形成环境变量 example / deployment 文档 / Runtime 配置注入。该项是治理接入缺口，
      本 Phase 只记录，不修改 Runtime。
      （注：该 Policy 已随 v1 归档至 `archive/memory-v1/`，缺口记录保留。）

## Release Safety

- [x] Runtime Hook 对 Pipeline / Java / Dispatcher 失败 fail-safe，不阻断 Agent 主响应。
- [x] Release Gate 对未通过的 safety / rollout / isolation / evaluation / cost 返回 BLOCKED。
- [x] 审计与评估结果不复制 userId、employeeId、conversationId、token、nonce 或完整 task payload。
- [x] 本清单不授权开启 `ENABLED`；生产启用必须另有配置、观测和运维确认。

## Final disposition

当前 Security Boundary 具备 commit 条件；两个未勾选项是已知的文档 / 契约治理事项，
不应通过删除校验或放宽边界来消除。它们应在后续专门变更中处理，并单独补测试。
