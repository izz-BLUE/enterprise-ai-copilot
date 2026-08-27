# 受控业务动作（Controlled Business Actions）

## 定位与边界

该能力是 PostgreSQL 持久化的受控 Sandbox，目前只支持 `ANNUAL_LEAVE_REQUEST`。PostgreSQL 是生产强依赖，数据库不可用时应用启动失败且不会降级到内存。它不接真实 OA、不使用 Redis 或消息队列。PendingAction、模拟余额、LeaveRequest 与 Planner-first 的 HITL Checkpoint 可跨 Java/Python/PostgreSQL 重启恢复。React 会展示脱敏后的 PendingAction 确认卡，并由用户显式确认或取消草稿。

Feature Flag `business.actions.enabled` 与 `demo.identity.enabled` 均默认关闭。共享 Admin Token 只用于演示访问控制，`X-Demo-User-Id` 只用于受控 Demo 数据隔离，两者都不代表员工身份认证。数据库终态和唯一 `source_action_id` 支持多 Java 实例间的确认重放；当前仍不处理中国法定节假日与调休。

main 同时保留两套 LangGraph 互斥状态图，由 `AGENT_LOOP_ENABLED` 切换：

- **Planner-first**（`AGENT_LOOP_ENABLED=true`，仓库部署默认）：`safety → planner ⇄ tool_executor → prepare_hitl → approval(interrupt) → finalize`。Planner 决策调用 `leave_proposal_tool`，同样复用 `tool_calling_service.plan_annual_leave_action` 生成 `action_proposal` 或 `missing_fields`；只有完整 Proposal 才进入一次 HITL wait。
- **legacy Router-first**（`AGENT_LOOP_ENABLED=false`，显式回退）：`safety → router → rag|eval|action|refuse`。`router_node` 检测到年假申请意图后进入 `action_node`，由 `tool_calling_service.plan_annual_leave_action` 生成 `action_proposal` 或 `missing_fields`。

两套图在受控业务动作这条链路上汇流到 **同一个 Java 权威控制面**：Python 端只产 Proposal，不执行写操作；`confirmationNonce`、PendingAction 持久化、状态机、TTL、幂等、权限和最终数据库写入全部由 Java 完成。

Planner-first 下受控业务动作相关的 Tool 可见性由程序层按权限动态收缩，**模型不能自行扩大 Tool 权限**：

- 始终可见：`rag_answer_tool`
- `employee_id`、`JAVA_BASE_URL`、`JAVA_INTERNAL_TOKEN` 均非空时追加：`leave_balance_tool` / `leave_request_tool`
- `allow_eval=true` 时追加：`eval_report_tool`
- `allow_business_actions=true` 且 `employee_id` 非空时追加：`leave_proposal_tool`（仅在此条件下 Planner 才能决策调用并产出 Proposal）

## 真实调用链

```mermaid
flowchart LR
    U[React / API Client] --> D[Demo Identity Directory]
    D --> J[Java Trace / Admin / Feature Flag]
    J --> S[Python Safety Guard]
    S --> G{AGENT_LOOP_ENABLED}
    G -->|false 显式回退| RT[Deterministic Router]
    G -->|true 仓库默认| P[Planner ⇄ Tool Executor]
    RT -->|字段完整| APP[action_proposal]
    RT -->|缺字段| CL[Clarification response]
    P -->|PlannerDecision| T[leave_proposal_tool]
    T -->|字段完整| APP
    T -->|缺字段| CL
    APP --> V[Java BusinessActionService / HITL Coordinator]
    V -->|Java 产 confirmationNonce + wait correlation| DB[(PostgreSQL PendingAction)]
    DB --> CARD[React PendingAction Card]
    CARD -->|confirm/cancel + owner + stable idempotency key| V
    V -->|事务提交后 best-effort resume| R[Python HITL resume]
    R -->|Command(resume) → finalize| CP[(PostgreSQL LangGraph Checkpoint)]
    V -->|confirm success| E[LeaveExecutionGateway]
    E --> L[(PostgreSQL Leave Account + LeaveRequest)]
    CARD -->|cancel| X[CANCELLED]
```

Safety Guard 先于一切；Planner-first 路径下若 Safety 拦截则直接终止，不会进入 Planner LLM。年假申请草稿不会进入只读 Tool：

- 政策 / 结转 / 审批流程类查询 → `rag_answer_tool`
- 余额查询 → `leave_balance_tool`
- 最近已成功提交的请假记录 → `leave_request_tool`
- 申请草稿（明确含"申请 / 提交 / 准备"年假业务动作）→ `leave_proposal_tool`

## `leave_proposal_tool` 的设计

`leave_proposal_tool` 是 Planner-first 下的受控业务动作 Tool。LLM 不接收用户原始问题、日期、reason、half-day、trace_id、policy context、员工信息、余额或 Admin Token，也不负责生成 Proposal。Tool Executor 独立做权限 / Tool 预算（`MAX_TOOL_CALLS=3`） / 成功签名去重校验；原始问题来自当前 `AgentState`，`business_date` / `trace_id` 等可信系统字段由 Executor 从本次请求 Runtime Context 注入，模型在 `arguments` 中不得夹带这些字段。

执行流程：

1. Planner 输出 `action=tool` 且 `tool_name=leave_proposal_tool`、`arguments={}`、`reason_code=need_proposal` 的严格结构化决策；
2. Tool Executor 在结构 / employee_id / 权限 / Tool 预算 / 成功签名去重校验通过后发起调用；
3. `leave_proposal_tool` 调用 `tool_calling_service.plan_annual_leave_action(question, business_date, trace_id)`；
4. 缺失日期或原因时返回 `kind=clarification`、`action_proposal=null`、`missing_fields=[...]`；
5. 字段完整时返回 `kind=proposal`、`action_proposal={...}`、`missing_fields=[]`；
6. Tool Executor 把 `action_proposal` / `missing_fields` 同步写回 `AgentState`；图终止后由 `_finalize_action_proposal` 与 `_finalize_response_contract` 收敛公共响应。

`leave_proposal_tool` **不依赖** `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN`；它不调用 Java 内部只读端点，不执行任何写操作，最终写操作完全交给 Java。

## Java 权威控制面

Java 使用入口 `TraceIdFilter` 生成的 traceId 作为 `PendingAction.originTraceId`，不信任 Python 响应中的 traceId。Admin Token 仅在 Java 内校验，不下传 Python。Java 使用配置时区的可注入 `Clock` 计算业务日期，并重新校验：

- Action 类型、日期完整性和日期顺序；
- 开始日期不得早于业务日期，跨度不超过 31 个日历日；
- reason 为 1 到 200 个非控制字符；
- 半天仅允许单个工作日；
- 工作日天数、余额和已有申请冲突。

整日申请只统计周一至周五，半天为 `0.5` 天。服务端固定目录提供 `DEMO-001`、`DEMO-002` 和 `DEMO-MGR-001` 三个互相隔离的账户；Manager 当前没有审批或查看他人申请的权限。

PendingAction 状态机：

```text
PENDING_CONFIRMATION
  ├─ confirm → PROCESSING → SUCCEEDED
  │                        └→ FAILED
  ├─ cancel  → CANCELLED
  └─ timeout → EXPIRED
```

`confirmationNonce` 由 Java 在 `BusinessActionService.createPending` 内部用 32 字节 `SecureRandom` 生成，明文只在创建响应返回；数据库只保存 32 字节 SHA-256 摘要，Java 使用常量时间比较。浏览器刷新后 nonce 不会恢复，因为明文仅存在页面内存。confirm 要求 UUID `Idempotency-Key`。

`createPending` 先按员工锁定 `leave_account` 并完成余额/冲突复核，再短时锁定 `business_action_control` 执行过期转换、全局容量判定和落库，缩短不同员工之间的串行区；活动 `(owner_user_id, conversation_id)` 另有数据库部分唯一索引兜底。冲突查询始终包含 employeeId，所以不同用户可提交相同日期，同一用户仍拒绝重叠日期。confirm/cancel 使用 `SELECT ... FOR UPDATE` 锁定 Action 行并先校验 owner；归属不符与 Action 不存在统一返回 `ACTION_NOT_FOUND`。confirm 再锁定当前 Account 行，在一个事务内通过 `LeaveExecutionGateway` 复查冲突、写入唯一 `source_action_id` 的 LeaveRequest、扣减余额并写入成功结果。任何数据库异常会整体回滚，草稿保持可重试。

同一 `(owner_user_id, conversation_id)` 至多一个活动 PendingAction（`PENDING_CONFIRMATION` / `PROCESSING`）。这是 `ai_task_memory` 以 `(user_id, conversation_id)` 为唯一键、每条会话只有一条任务记忆的配套约束：若允许同会话多个活动动作，任一动作进入终态都会收口整条会话 Memory，误伤其他仍在等待确认的动作的续接。`createPending` 在控制锁内检查该约束，命中返回 `409 ACTION_CONVERSATION_IN_PROGRESS`；`conversationId` 为 null（无 Memory 关联的历史路径）不限制。动作确认 / 取消 / 过期 / 失败后，同会话可再次发起新申请。

### Durable HITL User Confirmation Resume（P3-4）

Planner-first 的完整业务 Proposal 在 `prepare_hitl_node` 生成严格的 `HitlWaitMarker`：`schema_version=1`、固定 `kind`、确定性 `wait_id`、当前 `execution_id` 与 `action_type`。`approval_node` 只调用 LangGraph `interrupt(wait_marker)`，在恢复时严格校验 Java 返回的 `wait_id`、`execution_id`、`action_type` 和决定状态；interrupt 前后均不执行业务写操作。Clarification（`action_proposal=null`）不会创建 wait，也不会触发 Java PendingAction。

Python 先以 `durability=sync` 落盘 wait，再返回 Java 内部响应。Java `BusinessActionHitlCoordinator` 用 `agent_execution_id + hitl_wait_id` 注册唯一 PendingAction；相同 wait 的重试复用同一 action 行并只轮换尚未确认的 nonce，归属、会话、员工、动作类型或 execution 不一致时 fail-closed。HITL 的 correlation 字段是内部持久化元数据，不进入公网 `AgentChatResponse`。

Confirm / Cancel 与原 Agent Chat 共用 Java 的最终 runtime thread guard。Java 业务事务先提交后，才 best-effort 调用 Python `POST /agent/langgraph/hitl/resume`；Python 只接受严格的 Java-authoritative decision，重新注入当前 Runtime Context，不运行 Memory proposal pipeline。成功、取消、过期或安全拒绝都可幂等重放；Python 服务暂时不可用不会回滚已提交的 Java 业务结果，后续可由同一终态调用重试续接。普通 Chat 发现已有 wait 时只返回持久化 Proposal/wait 状态，不重新规划或覆盖它；wait、actor、capability 或 checkpoint 状态不匹配统一拒绝。

当前唯一实现 `PostgresLeaveSandboxGateway` 与 Action、账户处于同一个 PostgreSQL 事务。真实 OA 远程请求无法加入本地事务，不能声称"替换 Gateway 即可安全上线"；未来需要 Transactional Outbox、异步投递、外部幂等、回调或轮询、重试、对账、补偿和状态映射。

LeaveRequest 编号由 PostgreSQL Sequence 生成。事务回滚时 Sequence 已取出的编号不会回收，因此编号允许出现间隙，但不会因服务或数据库重启而重复。

confirm/cancel 请求体只允许 `confirmationNonce`，额外业务字段会被拒绝。PendingAction、confirm、cancel 及 Action 错误响应均使用 `Cache-Control: no-store`。

## Python 与 Java 的契约

Python 端通过 `/agent/langgraph/chat` 返回的内部响应字段（公网侧不直接消费）：

- `action_proposal`：确定性 Proposal 字典；仅在 Planner-first 或 legacy Router-first 命中 `route=action` 且字段完整时出现；
- `missing_fields`：缺字段时的固定顺序数组（`start_date` / `end_date` / `reason`）。

Java 端在 `LangGraphAgentController` 内：

1. 收到完整 `action_proposal` + Python 已落盘的 `hitl_wait` 后调用 `BusinessActionHitlCoordinator`；
2. 重新校验日期、跨度、半天、原因长度、工作日、余额与冲突；
3. 以 `agent_execution_id + hitl_wait_id` 唯一注册 PendingAction，生成 `confirmationNonce`，返回公网 `pendingAction` 视图（仅摘要 + nonce，不含内部 trace_id / 数据库主键 / 余额与申请历史）；
4. Confirm / Cancel 在同一 runtime thread guard 内执行，Java 事务提交后 best-effort 调用 Python 内部 resume；Python 只继续原 Checkpoint，不重跑 Planner、业务 Tool 或 Memory。

`action_proposal` 是 Java/Python 内部契约，**不能绕过 Java 权威校验直接执行**。

## React 人工确认链路

前端收到 PendingAction 后立即把 `confirmationNonce` 从可渲染响应中拆出，仅保存到当前页面生命周期内的 `useRef(Map)`；公开消息状态和 `PendingActionCard` props 中不包含 nonce。nonce、Admin Token 和幂等 Key 都不会写入 DOM、URL、日志、`localStorage` 或 `sessionStorage`。

Confirm 首次点击使用 `crypto.randomUUID()` 生成幂等 Key，并按消息保存在页面内存。网络失败、HTTP 502/503 或可重试服务端错误后，重试确认复用原 Key，避免服务端已成功但客户端未收到结果时重复执行。Cancel 不发送 `Idempotency-Key`。同步 `Set` 锁在 React 状态更新前生效，防止确认或取消的快速双击产生多个请求。

Confirm 首次成功后，Action 的持久化成功结果成为权威结果。后续使用相同或不同的格式合法 UUID `Idempotency-Key` 再次确认，均重放原 `requestId` 并返回 `replayed=true`，不会再次创建 LeaveRequest 或扣减余额。

卡片状态为 `pending → confirming → succeeded` 或 `pending → cancelling → cancelled`；过期草稿进入 `expired`，可重试错误进入 `error` 并且只显示与上一次决定一致的重试按钮。客户端到期计时用于提前禁用交互，服务端 `ACTION_EXPIRED` 仍是权威结果。执行中的动作会禁用清空会话、模式切换和身份选择。切换身份前对未处理草稿二次确认，确认后清空消息、nonce、Action UI、幂等 Key 和同步锁。身份只保存在 React state，不写入 localStorage、sessionStorage、Cookie 或 URL。

## P2-A Expense Workflow V1（差旅报销）

P2-A 在受控业务动作之上扩展 `EXPENSE_CLAIM`（Java `BusinessActionType`），
复用同一 `BusinessActionService` 通用生命周期（HITL / nonce / TTL / 幂等 /
Memory 收口），业务专属逻辑由 `BusinessActionHandler` 承载：

- `AnnualLeaveActionHandler`：年假业务（余额 / 冲突 / 执行 / summary）
- `ExpenseClaimActionHandler`：报销业务（proposal 校验 / 确定性金额计算 /
  写 ExpenseClaim + ExpenseItem / ExpenseClaimSummary）

分发点唯一：`proposal.actionType() → BusinessActionHandlerRegistry → handler`
（禁止 `instanceof` / `enum switch`）。

Python 侧新增 4 个 Agent Tool（V2 §六 / §九）：

| Tool | source | side_effect | identity_required | memory_eligible |
|---|---|---|---|---|
| `travel_record_tool` | MCP (`enterprise-oa-mcp`) | NONE | true | false |
| `invoice_verify_tool` | MCP (`enterprise-oa-mcp`) | NONE | true | false |
| `expense_proposal_tool` | LOCAL（受控 Proposal） | NONE | true | **true → EXPENSE_REQUEST** |
| `expense_status_tool` | Java `/api/internal/expense/*` | NONE | true | false |

`ExpenseActionProposal`（strict / extra='forbid' / action_type='EXPENSE_CLAIM'）
由程序层从 `tool_history` 中的成功 facts 确定性构造：
`tool_executor_node` 在调用 `expense_proposal_tool` 前从已成功的
travel / invoice / rag observation 组装 `ExpenseProposalContext`（系统注入，
不可由 LLM arguments 伪造）；Tool 内部**禁止**再次调用 MCP / Java / RAG。
金额 / 限额（HOTEL 750×晚封顶、其它合法实报）由确定性业务代码计算，
禁止 LLM 计算金额。

`expense_status_tool` 查询 Java 权威状态（Java Expense Domain 是最终
Source of Truth）；`GET /api/internal/expense/status?expenseId=` +
`/recent`，`X-Internal-Token` + 可信 `X-Employee-Id` 鉴权，ownership
check（跨员工 404）。

Memory：`EXPENSE_REQUEST` 只通过 `MemoryCapabilityRegistry`
（`EXPENSE_MEMORY_CAPABILITY`，eligible tool = `expense_proposal_tool`）
注册；其余 Expense read tools（travel / invoice / expense_status / rag）
单独成功不触发 Extractor；**未修改** Memory Trigger / Write / Extractor Core。

## 配置

| 配置 | 默认值 |
|---|---:|
| `business.actions.enabled` | `false` |
| `business.actions.require-admin` | `true` |
| `business.actions.ttl-seconds` | `600` |
| `business.actions.max-pending` | `100` |
| `business.actions.max-completed` | `500` |
| `business.actions.demo-annual-leave-balance` | `5.0` |
| `business.actions.timezone` | `Asia/Shanghai` |
| `demo.identity.enabled` | `false` |
| `AGENT_LOOP_ENABLED`（Python 端环境变量） | `true`（仓库部署默认，Planner-first；`false` 显式回退 legacy） |

仓库使用事务内惰性过期和数据库有界容量，不创建后台线程。`maxCompleted` 只清理未被 LeaveRequest 引用的 `CANCELLED / EXPIRED / FAILED`；成功 Action 保留，以满足外键和重放要求。本地表审计记录 traceId、originTraceId、actionId、状态变化、结果码和 requestId，不记录 Admin Token、nonce、nonce 摘要或完整 reason；完整集中日志 / APM / 告警栈尚未实现。

## 两套图对受控业务动作的影响

| 维度 | legacy Router-first（默认） | Planner-first（显式开启） |
|---|---|---|
| 入口 | `safety → router → action` | `safety → planner` 决策 → `leave_proposal_tool` |
| 是否暴露 `leave_proposal_tool` | 否（action_node 复用 `tool_calling_service`） | 由 `allow_business_actions` 控制可见性；Planner-first 最多 5 个 Tool，实际可见集合由程序层按权限动态收缩，模型不能自行扩大 Tool 权限 |
| 公共响应 `route=action` | 是 | 是 |
| 公共响应 `category=business_action` | 是 | 是 |
| 写操作入口 | 仍由 Java 完成 | 仍由 Java 完成 |
| 与 `BUSINESS_ACTIONS_ENABLED` 关系 | 同样依赖；不开启则 Java 不创建 PendingAction | 同样依赖；不开启则 `leave_proposal_tool` 在 Planner 看来不可见、Java 也拒绝 |
| 与 `JAVA_INTERNAL_TOKEN` 关系 | 无 | `leave_proposal_tool` 不依赖；只读 Tool 依赖 |

## 真实 OA 边界

当前 `PostgresLeaveSandboxGateway` 与 Action、账户参加同一个本地 PostgreSQL 事务，本项目没有发送任何真实 OA 请求。真实 OA 网络调用不能加入本地数据库事务，不能只替换 Gateway 就宣称安全上线；后续至少需要 Transactional Outbox、异步投递、外部幂等、重试、回调或轮询、对账、补偿和状态映射。
