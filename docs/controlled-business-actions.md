# Controlled Business Actions

## 定位与边界

该能力是 PostgreSQL 持久化的受控 Sandbox，目前只支持 `ANNUAL_LEAVE_REQUEST`。PostgreSQL 是生产强依赖，数据库不可用时应用启动失败且不会降级到内存。它不接真实 OA、不使用 Redis 或消息队列。PendingAction、模拟余额和 LeaveRequest 可跨 Java/PostgreSQL 重启恢复。React 会展示脱敏后的 PendingAction 确认卡，并由用户显式确认或取消草稿。

Feature Flag `business.actions.enabled` 与 `demo.identity.enabled` 均默认关闭。共享 Admin Token 只用于演示访问控制，`X-Demo-User-Id` 只用于受控 Demo 数据隔离，两者都不代表员工身份认证。数据库终态和唯一 `source_action_id` 支持多 Java 实例间的确认重放；当前仍不处理中国法定节假日与调休。

## 真实调用链

```mermaid
flowchart LR
    U[React / API Client] --> D[Demo Identity Directory]
    D --> J[Java Trace / Admin / Feature Flag]
    J --> S[Python Safety]
    S --> R[Deterministic Action Router]
    R --> I[AnnualLeaveInputService]
    I --> G{Missing Field Gate}
    G -->|缺字段| C[Deterministic Clarification]
    G -->|字段完整| T[Zero-Argument Native Tool]
    T --> P[Deterministic Proposal]
    P --> V[Java BusinessActionService]
    V --> A[(PostgreSQL PendingAction)]
    A --> R[React PendingAction Card]
    R -->|confirm + owner + stable idempotency key| E[LeaveExecutionGateway]
    E --> L[(PostgreSQL Leave Account + LeaveRequest)]
    R -->|cancel| X[CANCELLED]
```

Safety 先于 Evaluation，Evaluation 先于 Annual Leave Action，其他请求继续进入 RAG。年假政策、余额、结转和审批流程查询不会进入 Action Tool。

## 零参数 Native Tool

`plan_annual_leave_request` 是零参数受控协议节点，不是字段抽取器、业务事实来源或写操作 Tool。LLM 不接收用户原始问题、日期、reason、half-day、traceId、policy context、员工信息、余额或 Admin Token，也不负责生成 Proposal。受控 Tool Calling 使用独立 OpenAI SDK 客户端并显式设置 `max_retries=0`；应用层不递归、不循环重试，因此一次规划最多产生一次 Provider HTTP 请求。普通 RAG 客户端保持既有 SDK 行为。

最终契约：

```text
tool_name=plan_annual_leave_request
tool_count=1
parameters=omitted
tool_choice=Named
thinking=disabled
strict=omitted
retry_policy=none
max_attempts=1
max_tokens=64
provider_received_business_data=NO
```

Python 先确定性解析日期、明确原因表达和半天表达。缺少日期或原因时，Python 直接返回固定 Clarification，Provider 调用次数为 0。字段完整时，Provider 只需返回一个指定函数调用，且 `arguments` 必须能解析为严格空对象 `{}`；非空 Object、Array、非法 JSON、错误 Tool 名或多个 Tool Call 都会被拒绝。代码不读取 `message.content`，也不重试。

协议成功后，Proposal 的 `start_date`、`end_date`、`reason` 和 `half_day` 全部来自 Python 确定性分析结果。Provider 超时、连接失败、状态错误或 Tool 协议错误时不会生成草稿。

该设计的生产代价是：完整字段 Action 会额外依赖一次外部 Provider 调用；Provider 不可用时草稿生成失败；Native Tool 本身不增加业务语义，只提供可观测的协议门禁。

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

确认 nonce 由 32 字节 `SecureRandom` 生成，明文只在创建响应返回；数据库只保存 32 字节 SHA-256 摘要，Java 使用常量时间比较。浏览器刷新后 nonce 不会恢复，因为明文仅存在页面内存。confirm 要求 UUID `Idempotency-Key`。

`createPending` 先锁定 `business_action_control`，并发安全地执行过期转换、容量检查和历史清理；随后只锁定当前 identity 的 `leave_account`。冲突查询始终包含 employeeId，所以不同用户可提交相同日期，同一用户仍拒绝重叠日期。confirm/cancel 使用 `SELECT ... FOR UPDATE` 锁定 Action 行并先校验 owner；归属不符与 Action 不存在统一返回 `ACTION_NOT_FOUND`。confirm 再锁定当前 Account 行，在一个事务内通过 `LeaveExecutionGateway` 复查冲突、写入唯一 `source_action_id` 的 LeaveRequest、扣减余额并写入成功结果。任何数据库异常会整体回滚，草稿保持可重试。

当前唯一实现 `PostgresLeaveSandboxGateway` 与 Action、账户处于同一个 PostgreSQL 事务。真实 OA 远程请求无法加入本地事务，不能声称“替换 Gateway 即可安全上线”；未来需要 Transactional Outbox、异步投递、外部幂等、回调或轮询、重试、对账、补偿和状态映射。

LeaveRequest 编号由 PostgreSQL Sequence 生成。事务回滚时 Sequence 已取出的编号不会回收，因此编号允许出现间隙，但不会因服务或数据库重启而重复。

confirm/cancel 请求体只允许 `confirmationNonce`，额外业务字段会被拒绝。PendingAction、confirm、cancel 及 Action 错误响应均使用 `Cache-Control: no-store`。

## React 人工确认链路

前端收到 PendingAction 后立即把 `confirmationNonce` 从可渲染响应中拆出，仅保存到当前页面生命周期内的 `useRef(Map)`；公开消息状态和 `PendingActionCard` props 中不包含 nonce。nonce、Admin Token 和幂等 Key 都不会写入 DOM、URL、日志、`localStorage` 或 `sessionStorage`。

Confirm 首次点击使用 `crypto.randomUUID()` 生成幂等 Key，并按消息保存在页面内存。网络失败、HTTP 502/503 或可重试服务端错误后，重试确认复用原 Key，避免服务端已成功但客户端未收到结果时重复执行。Cancel 不发送 `Idempotency-Key`。同步 `Set` 锁在 React 状态更新前生效，防止确认或取消的快速双击产生多个请求。

Confirm 首次成功后，Action 的持久化成功结果成为权威结果。后续使用相同或不同的格式合法 UUID `Idempotency-Key` 再次确认，均重放原 `requestId` 并返回 `replayed=true`，不会再次创建 LeaveRequest 或扣减余额。

卡片状态为 `pending → confirming → succeeded` 或 `pending → cancelling → cancelled`；过期草稿进入 `expired`，可重试错误进入 `error` 并且只显示与上一次决定一致的重试按钮。客户端到期计时用于提前禁用交互，服务端 `ACTION_EXPIRED` 仍是权威结果。执行中的动作会禁用清空会话、模式切换和身份选择。切换身份前对未处理草稿二次确认，确认后清空消息、nonce、Action UI、幂等 Key 和同步锁。身份只保存在 React state，不写入 localStorage、sessionStorage、Cookie 或 URL。

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

仓库使用事务内惰性过期和数据库有界容量，不创建后台线程。`maxCompleted` 只清理未被 LeaveRequest 引用的 `CANCELLED / EXPIRED / FAILED`；成功 Action 保留，以满足外键和重放要求。审计日志记录 traceId、originTraceId、actionId、状态变化、结果码和 requestId，不记录 Admin Token、nonce、nonce 摘要或完整 reason。
