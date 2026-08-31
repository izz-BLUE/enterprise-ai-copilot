# API 契约审计

本文按当前 Controller、FastAPI route 和 Mock OA app 审计接口。公网调用只应使用 Java；Python、Java `/api/internal/**` 和 Mock OA admin 端点是服务间/演示内部接口。

## 1. 服务地址

| 服务 | 本地地址 | 暴露边界 |
|---|---|---|
| React | `http://localhost:5173` | 浏览器 UI |
| Java | `http://localhost:8080` | 唯一业务/API gateway |
| Python | `http://localhost:8000` | Java 内部；生产 Compose 不映射宿主机端口 |
| Mock OA | `http://localhost:8010` | 独立模拟审批服务 |

Java 入口为可信边界：它解析 JWT、生成 `trace_id`、决定 Admin/capability、解析 conversation scope，并向 Python 注入可信 runtime headers。客户端不能通过 body 或 LLM arguments 覆盖 employee、权限、业务日期或 trace。

## 2. Java 公共 API

### 认证

| Method | Path | Auth | 说明 |
|---|---|---|---|
| `POST` | `/api/auth/login` | public | 用户名/密码登录并建立 token/cookie |
| `POST` | `/api/auth/logout` | authenticated | 注销当前认证上下文 |
| `GET` | `/api/auth/me` | authenticated | 返回当前用户身份 |

生产和本地 Demo 均使用 JWT；Demo Auth 只负责初始化固定演示账号。

### 健康检查与版本

| Method | Path | Auth | 说明 |
|---|---|---|---|
| `GET` | `/api/health` | public | Java 存活状态 |
| `GET` | `/api/ready` | public | Java 就绪状态 |
| `GET` | `/api/agent/health` | public | Java 代理查询 Python 健康状态 |
| `GET` | `/api/agent/ready` | public | Java 代理查询 Python 就绪状态 |
| `GET` | `/api/version` | public | 构建/版本元数据 |

### Chat 接口

| Method | Path | Auth | 说明 |
|---|---|---|---|
| `POST` | `/api/chat` | authenticated | 稳定 RAG 问答 |
| `POST` | `/api/agent/langgraph/chat` | authenticated | Planner-first 或显式 Router-first Agent |

请求体：

```json
{
  "message": "病假需要提供哪些材料？",
  "conversationId": "optional-client-scope"
}
```

`message` 非空且最多 2000 字符；`conversationId` 可选，最长 64 字符，只允许字母/数字和 `._-:`。缺失时 Java 生成独立 scope。

稳定 RAG 响应核心字段：

```json
{
  "answer": "根据知识库……",
  "model": "deepseek",
  "traceId": "server-generated-uuid",
  "success": true,
  "sources": ["hr-policy.md#chunk-12"]
}
```

Agent 响应核心字段：

```json
{
  "answer": "……",
  "route": "rag|eval|action|refuse|error",
  "safe": true,
  "category": "general|business_action|…",
  "reason": "stable-safe-reason",
  "sources": [],
  "success": true,
  "traceId": "server-generated-uuid",
  "pendingAction": null
}
```

完整 Proposal 会产生 `pendingAction`；内部 `action_proposal`、`hitl_wait`、`external_wait` 不复制到浏览器响应。缺字段只返回 `missing_fields` 所表达的 Clarification，不创建 PendingAction。

### 受控业务动作

| Method | Path | Auth | Headers/body |
|---|---|---|---|
| `POST` | `/api/agent/actions/{actionId}/confirm` | owner + capability | JSON `{"confirmationNonce":"..."}`；必须带 UUID `Idempotency-Key` |
| `POST` | `/api/agent/actions/{actionId}/cancel` | owner + capability | JSON `{"confirmationNonce":"..."}` |

Confirm 响应：

```json
{
  "actionId": "…",
  "type": "ANNUAL_LEAVE_REQUEST",
  "status": "SUCCEEDED",
  "requestId": "…",
  "message": "…",
  "replayed": false,
  "completedAt": "2026-08-28T00:00:00Z",
  "originTraceId": "…",
  "traceId": "…",
  "nextPendingAction": null
}
```

`type` 当前为 `ANNUAL_LEAVE_REQUEST` 或 `EXPENSE_CLAIM`；Action status 为 `PENDING_CONFIRMATION`、`PROCESSING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`EXPIRED`。Confirm 的重复 UUID key 重放原结果；Action 不存在或 owner 不匹配统一收敛为安全的 not-found 语义。常见业务冲突为 409，revalidation 不可用为 503，系统错误为 5xx；响应不暴露 nonce digest、token 或内部异常。

Multi Task Runtime 的确认响应在 Java 已提交当前动作后，可能附带下一任务的 `nextPendingAction`。该字段仍是 Java PendingAction 视图；不存在时为 `null`。Task Runtime 的 Expense 确认会先让当前 Python task graph `END`，再由 Java 绑定 ExpenseClaim 的外部关联并提交 OA，不进入 Python `/agent/langgraph/external/resume`。

### 管理与 Demo 端点

| Method | Path | Auth | 说明 |
|---|---|---|---|
| `GET` | `/api/admin/logs` | `ROLE_ADMIN` | 管理员诊断日志缓冲区 |

浏览器的 eval/管理能力由 Java 已验证 JWT 的 `role=ADMIN` 授权；浏览器不发送 `X-Admin-Token`。`X-Admin-Token` 仅作为内部业务动作 hardening 的兼容 header，`BUSINESS_ACTIONS_REQUIRE_ADMIN=true` 时由 Java 校验匹配的 server-only `ADMIN_TOKEN`；它不是普通用户认证替代品。

## 3. Java 内部读取 API

这些端点在 Spring Security 中以内部链路 permit，但必须同时提供 Java 侧配置的 `X-Internal-Token` 和可信上游注入的 `X-Employee-Id`。它们按 employee ownership 查询，不能用浏览器输入的身份。

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/internal/leave/balance` | `leave_balance_tool` 查询当前员工余额 |
| `GET` | `/api/internal/leave/requests?limit=20` | `leave_request_tool` 查询当前员工成功记录 |
| `GET` | `/api/internal/expense/status?expenseId=...` | `expense_status_tool` 查询当前员工 ExpenseClaim |
| `GET` | `/api/internal/expense/recent?limit=20` | 当前员工最近报销摘要 |

跨员工查询按 not-found 处理；token 缺失或 employee header 缺失不会获得业务数据。

## 4. Java Mock OA webhook

唯一 webhook 路径：

```text
POST /api/webhooks/mock-oa/expense-approval
```

只有这个精确 POST path 在 webhook 这一类路径中由 SecurityConfig permitAll；业务 API 仍受正常认证保护，健康检查、版本和 token-protected internal read routes 另有各自契约。请求头：

```text
X-Mock-OA-Timestamp: <unix-seconds>
X-Mock-OA-Signature: v1=<hex-hmac>
```

签名覆盖 `timestamp + "." + rawBody`，共享 secret 为 `MOCK_OA_WEBHOOK_SECRET`，timestamp 默认最大偏差 300 秒。严格 body 只允许：

```json
{
  "eventId": "evt_…",
  "eventType": "EXPENSE_APPROVAL_CHANGED",
  "requestId": "oa_…"
}
```

body 不包含 status。Java 验签和解析成功后，以 `requestId` 调 Mock OA GET 查询权威状态，再幂等更新 ExpenseClaim。状态 sync 成功返回 204；签名失败 401；body/schema/event 失败 400；权威查询或处理失败 502。

## 5. Python 内部 API

Python 不对公网提供业务入口；这些接口由 Java 或运行时内部调用。

| Method | Path | 说明 | 成功/错误语义 |
|---|---|---|---|
| `GET` | `/agent/health` | Python 存活状态 | 200 |
| `GET` | `/agent/ready` | Python 就绪状态，含 Checkpoint 可用性 | 不可用时非 2xx |
| `GET` | `/agent/version` | 构建/版本元数据 | 200 |
| `POST` | `/agent/chat` | Java → Python 稳定 RAG | `ChatResponse` |
| `POST` | `/agent/tasks/decompose` | Java → Python 的无状态确定性双任务分解 | 只返回 `single`、`multi` 或 `unsupported`；不写 Checkpoint/业务状态 |
| `POST` | `/agent/langgraph/chat` | Java → Python Agent | 200；busy 429；recovery conflict 409；checkpoint unavailable 503；运行失败 502 |
| `POST` | `/agent/internal/expense/revalidate` | Java 确认时窄适配器 | 成功返回当前 trip/invoice facts；不可用时 fail-closed |
| `POST` | `/agent/langgraph/hitl/resume` | Java 权威的用户确认恢复 | 只接受严格 HITL payload |
| `POST` | `/agent/langgraph/external/resume` | Java 权威的外部审批恢复 | 只接受严格 external payload |

内部 Agent 请求使用 Java 注入的 `X-Agent-Thread-Id`、`X-Employee-Id`、`X-Conversation-Id`、`X-Business-Date`、`X-Allow-Eval` 和 `X-Allow-Business-Actions`。Task Runtime 另外使用 `X-Agent-Execution-Mode: TASK_RUNTIME` 与 Java 生成的 `X-Agent-Task-Id`；单业务兼容路径使用 `LEGACY_SINGLE`。Python 只把 execution mode 放在 trusted Runtime Context，LLM 和 AgentState 不能选择 external lifecycle。Python 以 schema、latest checkpoint 和 correlation 再校验，不信任 payload 中可伪造的 owner/permission。

### 确认时重新校验 payload

请求体由 Java 从持久化 Action 生成：

```json
{
  "schema_version": 1,
  "employee_id": "employee-from-java",
  "trip_id": "trip-001",
  "invoice_ids": ["invoice-001", "invoice-002"]
}
```

适配器返回 schema version、`success`/`error_code`、当前 trip 日期/状态和 invoice facts。Java 不接受浏览器或 Memory 提供金额；它根据返回事实确定性重算。Stale 与 unavailable 的 Java 处理见 [Controlled Business Actions](controlled-business-actions.md)。

## 6. Mock OA API

Mock OA 不是浏览器 API，是独立模拟外部审批服务。

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/api/expense-approvals` | 创建或幂等重放审批请求；必须 `Idempotency-Key`，格式 `expense:<expenseId>` |
| `GET` | `/api/expense-approvals/{requestId}` | 查询当前权威状态 |
| `POST` | `/api/admin/expense-approvals/{requestId}/approve` | Demo 管理面批准 |
| `POST` | `/api/admin/expense-approvals/{requestId}/reject` | Demo 管理面拒绝 |

提交 body：

```json
{
  "expenseId": "expense-001",
  "employeeId": "employee-001",
  "tripId": "trip-001",
  "costCenter": "CC-001",
  "claimedAmount": "500.00",
  "reimbursableAmount": "450.00"
}
```

响应：`{"requestId":"oa_…","status":"PENDING"}`，或幂等 replay 时返回当前 `APPROVED/REJECTED`。相同 key 的 payload hash 不一致返回 409；不存在 request 返回 404；反向终态决定返回 409。Mock OA 在终态提交后才 best-effort webhook，webhook 失败不回滚 OA 状态。

### 6.1 Java 管理员审批 Facade

公网浏览器只能访问下面的 Java 路径。Java 从已验证 JWT 的 `role=ADMIN` 授权后，在服务端调用 Mock OA；浏览器不拼接 Mock OA 地址，也不发送 `ADMIN_TOKEN` 或 `X-Admin-Token`。

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/admin/mock-oa/expense-approvals` | 查询最近最多 100 条审批，可用 `status=PENDING\|APPROVED\|REJECTED` 筛选 |
| `POST` | `/api/admin/mock-oa/expense-approvals/{requestId}/approve` | 请求 Mock OA 批准 |
| `POST` | `/api/admin/mock-oa/expense-approvals/{requestId}/reject` | 请求 Mock OA 拒绝 |

列表只返回 `requestId`、`status`、`expenseId`、`employeeId`、`tripId`、`costCenter`、`claimedAmount`、`reimbursableAmount`、`createdAt`。`idempotency_key`、`payload_hash`、webhook secret 和其他内部 credential 永不返回浏览器。成功审批只代表 Mock OA 已提交终态；页面会提示该结果将通过 webhook 异步同步到 Java 业务系统。

Java Facade 将 Mock OA 的不存在、状态冲突、不可用和超时分别映射为 404、409、502/503；超时提示结果未知，前端不会把它渲染成业务失败终态。Mock OA 未启用时返回 503，不静默成功。

## 7. 常用请求头与错误

| Header | 来源/用途 |
|---|---|
| `Authorization: Bearer ...` | Java 公共认证 |
| `X-Admin-Token` | 仅内部业务动作 hardening 的兼容 header；浏览器不发送，不是身份 |
| `Idempotency-Key` | Confirm 必需 UUID；Mock OA 使用 `expense:<expenseId>` |
| `X-Trace-Id` | Java 生成并透传；客户端值不作为 authority |
| `X-Conversation-Id` | Java 解析后的 conversation scope |
| `X-Agent-Thread-Id` | Java 根据可信 user/conversation 生成 |
| `Retry-After` | 429 busy/overload 的重试提示 |

公共错误响应使用稳定的 `success=false`/错误 code/traceId 语义，不返回 Python/Java exception message、secret、nonce digest 或原始 webhook body。业务 action 的具体错误码以 Java `ActionErrorResponse` 为准；文档不声称不存在的路由或旧的状态 endpoint。

## 8. 契约审计说明

- Java `/api/chat` 与 `/api/agent/langgraph/chat` 是浏览器/外部客户端的业务入口；Python 对应路由是内部 gateway 契约。
- `/api/internal/leave/**`、`/api/internal/expense/**` 是 Python read tools 的内部读取接口，不是公网业务 API。
- `/agent/internal/expense/revalidate` 是 Java 确认时 adapter，不是前端验证接口。
- `/agent/tasks/decompose` 是无状态、程序层确定性 decomposition；Java 校验 `task_text` 必须是原文有序连续 span，并负责 TaskExecution 生命周期。
- `/api/webhooks/mock-oa/expense-approval` 是唯一 Mock OA webhook receiver；通知没有 status authority。
- `WAITING_USER` 与 `WAITING_EXTERNAL` 使用不同的 Python resume endpoint；普通 chat 不跨 active wait。
- 当前没有公开的 Java ExpenseClaim status 查询 API；Expense status read tool 使用上述 internal endpoint。
