# 接口文档

## 公网演示地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 公网演示 | `https://copilot.jintianchi.cn` | React 前端 + Java API |
| 健康检查 | `https://copilot.jintianchi.cn/api/health` | Java 健康 |
| 就绪检查 | `https://copilot.jintianchi.cn/api/ready` | Java、数据库与 Python 依赖就绪状态 |
| Python 健康 | `https://copilot.jintianchi.cn/api/agent/health` | Python 健康（通过 Java 代理） |

**公网说明：**
- Python 接口不公网暴露，仅通过 Java `/api/*` 代理访问
- API 限流：2 req/s，burst 5（超限返回 JSON 429，并包含 `Retry-After: 1`）
- 公网 Demo 环境，非生产 SLA

公网入口限流发生在 Java 之前。此时响应中的 `traceId` 来自 Nginx `$request_id`；进入 Java 的请求仍由 `TraceIdFilter` 生成 UUID v4 traceId。

## 本地服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| Java Spring Boot | `http://localhost:8080` | 业务网关，统一入口 |
| Python FastAPI | `http://localhost:8000` | AI 引擎，RAG + Agent |
| React Frontend | `http://localhost:5173` | 前端演示页面 |

## Java Backend API（端口 8080）

### POST /api/auth/login

使用 `app_user` 中的用户名和密码登录，返回短期 Bearer JWT，并为浏览器设置 `HttpOnly`、`SameSite=Strict` 的认证 Cookie。前端生产构建不预填用户名或密码。

```json
{"username":"zhangsan","password":"<password>"}
```

登录成功响应为 API/压测客户端保留 `accessToken`、`tokenType`、`expiresIn` 和用户信息。浏览器前端不把 Token 写入 Web Storage，后续使用 HttpOnly Cookie。JWT 只携带已验证的身份字段，不携带 `enabled`；账号是否启用仅在登录时查询数据库。

### POST /api/auth/logout

需要有效身份。清除浏览器认证 Cookie，返回 `204 No Content`。Cookie 驱动的非只读请求必须携带 `X-Requested-With: XMLHttpRequest`；Bearer API 客户端不受此约束。

### GET /api/auth/me

返回当前 JWT 身份。浏览器使用 HttpOnly Cookie；API 客户端使用 `Authorization: Bearer <access-token>`。Agent 和 Business Action 路由的 Spring Security 规则均为 `authenticated()`：有效 Bearer JWT 优先；凭据存在但无效或过期直接返回 401，不回退 Demo 身份；完全没有凭据时，受控 Demo 模式才可使用 `X-Demo-User-Id` fallback。

`/api/internal/leave/**` 与 `/api/internal/expense/**` 不要求用户 JWT，仅由 `X-Internal-Token` 服务间认证，并消费可信上游注入的 `X-Employee-Id`。

### GET /api/internal/expense/status（P2-A）

内部只读接口：供 Python `expense_status_tool` 查询 Java 权威报销状态。

```http
GET /api/internal/expense/status?expenseId=EXP-20260826-000001
X-Internal-Token: <internal-token>
X-Employee-Id: DEMO-001
X-Trace-Id: <trace-id>
```

响应（Java 权威，`Cache-Control: no-store`）：
```json
{
  "expenseId": "EXP-20260826-000001",
  "status": "SUBMITTED",
  "claimedAmount": 1830.00,
  "reimbursableAmount": 1730.00,
  "tripId": "TRIP-20260818-001",
  "submittedAt": "2026-08-26T10:00:00Z"
}
```

`expense.employeeId` 必须等于可信 `X-Employee-Id`，跨员工读取返回 404
`EXPENSE_NOT_FOUND`（V2 §二十四）。错误码：`EXPENSE_READ_DISABLED`（503）/
`EXPENSE_READ_FORBIDDEN`（403）/ `EXPENSE_ID_REQUIRED`（400）/
`EXPENSE_NOT_FOUND`（404）。

### GET /api/internal/expense/recent（P2-A，可选）

按员工拉取最近报销单（`limit` 可选，默认 10，最大 50）。响应
`{ employeeId, total, items: [ExpenseStatusResponse...] }`。

### GET /api/health

Java 进程存活检查，不访问下游依赖。

**响应**
```json
{
  "service": "backend-java",
  "status": "UP"
}
```

---

### GET /api/ready

Java 就绪检查：验证数据库连接和 Python `/agent/ready`。任一依赖未就绪时返回 HTTP 503 与 `status=NOT_READY`，容器编排使用此接口摘除实例。

---

### GET /api/agent/health

Java 代理 Python 存活检查。Python 依赖就绪状态使用 `/api/agent/ready`。

**响应**（转发 Python 原始响应）
```json
{
  "service": "agent-python",
  "status": "UP",
  "concurrency": {
    "maxConcurrent": 3,
    "active": 0,
    "available": 3,
    "rejected": 0,
    "queueTimeoutMs": 500
  }
}
```

---

### POST /api/chat

**稳定 RAG 主链路。** Java 代理 Python `/agent/chat`。

> **安全说明：** RAG 主链路会先经过 Safety Guard 前置检查。高风险问题（如伪造、违法、攻击等）会被直接拦截并返回安全拒答文案，不会进入检索和 LLM 调用。安全拒答时 `success=true`，表示系统成功处理并拒绝了高风险请求。当前 Safety Guard 是规则版基础防护（5 类风险关键词匹配），不是完整安全系统。

**请求**
```json
{"message": "病假需要提供哪些材料？"}
```

**响应**
```json
{
  "answer": "根据企业知识库，病假需要提供：\n1. 病历本复印件\n2. 缴费清单\n3. 病假证明",
  "model": "deepseek-v4-flash",
  "traceId": "551245e6-a04b-442d-adef-99387f93cd23",
  "success": true,
  "sources": ["hr/leave-policy.md#chunk-3"]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| model | string | 大模型名称 |
| traceId | string | 请求追踪 ID（全链路一致） |
| success | bool | 是否成功 |
| sources | list[string] | 可读来源定位（领域/文件名 + chunk 序号） |

标准问答与 Agent RAG Tool 共用同一 RAG 服务与来源映射。

**异常响应**（Python 不可用时）
```json
{
  "answer": "当前 AI 服务暂时不可用，请稍后重试。",
  "model": "unknown",
  "traceId": "c6d65330-7aca-4c1e-895e-c5f6b6e24a94",
  "success": false
}
```

**超长输入响应**（message 超过 2000 字符）
```json
{
  "answer": "输入内容过长，请精简后重试。",
  "model": "deepseek-chat",
  "traceId": "xxx",
  "success": false
}
```

**应用过载响应**（Java 或 Python 并发槽在 500ms 内不可用，HTTP 429，包含 `Retry-After: 1`）
```json
{
  "answer": "当前请求较多，请稍后重试。",
  "model": "unknown",
  "traceId": "xxx",
  "success": false
}
```

> **请求边界：**
> - Java 侧通过 `@Size(max=2000)` 校验输入长度，Python 侧通过 `MAX_MESSAGE_LENGTH` 兜底校验。
> - Java 调 Python 配置了连接超时（3s）和读取超时（40s）。
> - Python LLM 调用配置了超时（默认 30s），超时或连接失败会返回错误响应。
> - CORS 已从 `*` 收敛为可配置白名单（`cors.allowed-origins`）。
> - 以上不改变正常 RAG 响应结构。

**适用场景**：企业制度、流程、IT 文档、HR 文档等知识库问答。

---

### POST /api/agent/langgraph/chat

**实验性 Agent 链路。** Java 代理 Python `/agent/langgraph/chat`，支持 Safety Guard + 意图路由 + Tool Calling。

**请求**
```json
{"message": "病假需要提供哪些材料？", "conversationId": "leave-demo-01"}
```

`conversationId` 是同一登录用户下的会话命名空间，不是身份字段。前端在 Agent 会话生命周期内复用它；缺失、空值或非法值时 Java 生成新的 UUID，并通过响应头 `X-Conversation-Id` 返回本次实际值。Java 始终以 `(trusted user_id, conversation_id)` 复合 key 读取和写入 Memory，客户端不能通过 conversationId 访问其他用户的数据。

**响应**（RAG 问答）
```json
{
  "answer": "根据企业知识库，病假需要提供...",
  "route": "rag",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": ["hr/leave-policy.md#chunk-3", "hr/leave-faq.md#chunk-1"],
  "success": true,
  "traceId": "387af8a3-5357-4a0c-8e48-77505524a8f3"
}
```

**响应**（评估查询，v0.3.2+ 返回实际指标）
```json
{
  "answer": "检索评估: 28/28 通过, final_pass_rate=1.0；生成评估: 38/38 通过, pass_rate=1.0, stable_pass_rate=0.9737, flaky=1",
  "route": "eval",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": [],
  "success": true,
  "traceId": "..."
}
```

`route=eval` 在 legacy Router-first 与 Planner-first 两种状态下都可能产生；公共响应字段一致。

**响应**（安全拒答）
```json
{
  "answer": "抱歉，我不能协助处理该请求。",
  "route": "refuse",
  "safe": false,
  "category": "illegal_or_policy_violation",
  "reason": "检测到高风险关键词「伪造」，属于「违法违规 / 伪造材料」类别。",
  "sources": [],
  "success": true,
  "traceId": "..."
}
```

**响应**（完整年假申请，Java 公网契约）

Agent 模式需要已认证身份：公网/正常登录链路携带 `Authorization: Bearer <access-token>`；仅在显式启用 Demo fallback 且完全没有 Bearer 时，才可携带来自服务端演示身份目录的 `X-Demo-User-Id`。Java 不会把用户提交的 employeeId、角色、余额或申请历史发送给 Python/模型。

Python `leave_proposal_tool` 只在 Planner-first 下被 Planner 决策调用，生成 `action_proposal`（完整字段）或 `missing_fields`（Clarification），**不执行写操作**。完整 Proposal 会先在 Python PostgreSQL Checkpoint 中持久化一次 `HitlWaitMarker`，再由 Java `LangGraphAgentController` / `BusinessActionHitlCoordinator` 重新执行权限、日期、工作日、余额和冲突校验，以 `agent_execution_id + hitl_wait_id` 唯一注册 PendingAction；`confirmationNonce` 由 Java 生成，DB 仅存 SHA-256 摘要。相同 wait 的请求重试复用原 action 行并轮换未确认 nonce，归属或 correlation 不一致时拒绝。校验通过后，公网响应仍只暴露可确认的 `pendingAction`：

```json
{
  "answer": "我已生成一份模拟年假申请草稿，请确认后提交。",
  "route": "action",
  "safe": true,
  "category": "business_action",
  "reason": "",
  "sources": [],
  "success": true,
  "traceId": "...",
  "pendingAction": {
    "actionId": "...",
    "type": "ANNUAL_LEAVE_REQUEST",
    "status": "PENDING_CONFIRMATION",
    "title": "提交模拟年假申请",
    "summary": {
      "displayName": "Demo User",
      "startDate": "2026-07-20",
      "endDate": "2026-07-20",
      "halfDay": "NONE",
      "days": 1.0,
      "reason": "示例原因",
      "balanceBefore": 5.0,
      "balanceAfter": 4.0
    },
    "confirmationNonce": "仅在本次响应返回的确认凭据",
    "expiresAt": "2026-07-20T10:10:00Z",
    "confirmationRequired": true
  }
}
```

该响应使用 `Cache-Control: no-store`。`traceId` 来自 Java 入口；它同时作为 PendingAction 的权威 `originTraceId`，不采用 Python 响应中的 traceId。`hitl_wait`、`agent_execution_id` 和 `hitl_result` 都是 Java-Python 内部契约，不出现在公网 `AgentChatResponse`。

React 收到响应后立即从 PendingAction 中拆出 `confirmationNonce`。公开消息状态和确认卡只保留草稿摘要；nonce 仅保存在页面内存 `Map` 中，不进入 DOM、日志或浏览器持久化存储。

**响应**（年假申请缺字段）

缺少日期或原因时，Python 返回确定性 Clarification，Provider 调用次数为 0，Java 不创建 PendingAction：

```json
{
  "answer": "请提供明确的年假日期。",
  "route": "action",
  "safe": true,
  "category": "business_action",
  "reason": "",
  "sources": [],
  "success": true,
  "traceId": "..."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| route | string | 公共路由分类：`rag` / `eval` / `action` / `agent` / `refuse` / `busy` / `error` |
| safe | bool | 安全守卫是否通过 |
| category | string | 公共分类：`normal` / `access_control` / `business_action` / `recovery_conflict` / `overloaded` / `error` / `input_error`；Safety Guard 命中时由 Safety 写入细分类别（`illegal_or_policy_violation` / `policy_bypass` / `cybersecurity_attack` / `audit_tampering` / `unauthorized_access`），公网消费方按需处理 |
| reason | string | 拒答原因（安全 / 权限场景）。正常完成 / 业务动作场景为空字符串；异常场景下为空字符串，异常详情不返回给用户，仅记录在服务端日志中 |
| sources | list | 可读来源定位列表（领域/文件名 + chunk 序号） |
| success | bool | 是否成功；语义：`route != 'error'` ⇒ `success=true`，合法拒绝 / 权限拒绝均视为成功（系统已正确处理），仅技术 / 规划失败返回 `false` |
| traceId | string | 请求追踪 ID |
| pendingAction | object/null | Java 权威校验后创建的待确认动作；仅完整 Action 返回 |

**权限行为（v0.3.2+）：**

- 普通 RAG 问答和 Safety Guard 拒答不受权限影响
- 公网部署（v0.3.2+）`ADMIN_TOKEN` 必须为非空值，Compose 启动时强制校验
- Evaluation 查询需要管理员权限：请求头 `X-Admin-Token` 必须匹配才允许 eval 路由
- 本地开发 `admin.token` 可为空（Demo 模式），所有用户均可访问 Evaluation
- `X-Admin-Token` 由调用方发送给 Java 后端，不应由前端 role 代替（权限判断在 Java 后端完成）
- `X-Allow-Eval` 是 Java → Python 内部 header，表示 Java 已完成权限判断，**不是认证凭证**，Python 不应将其当作独立安全边界

**正常权限拒绝示例**（程序层明确拒绝 Evaluation，`admin.token` 非空且未提供正确的 `X-Admin-Token`）
```json
{
  "answer": "该问题涉及内部评估诊断能力，仅管理员可访问。",
  "route": "refuse",
  "safe": true,
  "category": "access_control",
  "reason": "",
  "sources": [],
  "success": true,
  "traceId": "xxx"
}
```

Planner-first 还存在独立的 Planner contract 语义：若 Planner 输出当前 Capability Gate 未暴露的 Tool，系统拒绝该非法规划；其稳定公共响应为 `route=error`、`category=error`、`success=false`。这不等同于普通权限拒绝，也不意味着所有无权限 Evaluation 请求都会返回 error；正常权限拒绝仍可表现为 `route=refuse`、`category=access_control`。反之，也不能把隐藏 Tool 的 contract violation 归类为 access control。

**Scoped Conversation Memory P0：**

- Java Read Path 只把当前 `(trusted user_id, conversationId)` 命中的 `ACTIVE` 记录转换为内部 `memoryContext`，注入 Python Planner；`COMPLETED` / `ABANDONED` 不注入。
- Python Memory Extractor 消费不可信的历史任务数据，经过 `MemoryWritePolicy` 的 trusted-key 递归过滤、16 KiB task state 和 500 字符 summary 限制后才形成 command。
- `MEMORY_WRITE_MODE=DISABLED`（默认）不调用 Extractor；`AUDIT_ONLY` 运行 Trigger/Extractor/Policy 但只记录元数据；`ENABLED` 在 Python Agent 响应内返回 `memory_proposal`。
- `memory_proposal` 不包含 owner、conversationId、action 或 status。Java 使用当前 `VerifiedIdentity.userId()` 与服务端解析的 conversationId，固定按 `UPSERT + ACTIVE` 持久化。
- Memory 写入受状态机白名单约束（无记录仅允许 ACTIVE；终态不可重新激活），非法转换返回 `409 MEMORY_STATE_CONFLICT` 且不落库。
- Memory 终态由 Java 收口：PendingAction 状态变更（确认成功 → COMPLETED；取消 / 过期 / 处理失败 → ABANDONED）在同一事务内终结对应 ACTIVE memory；动作创建失败时不写入该次 Memory 提案。
- 同一 `(user_id, conversationId)` 至多一个活动 PendingAction：`ai_task_memory` 以该复合 key 为唯一键、每条会话只有一条任务记忆，因此 Java 在 `createPending` 内（控制锁内）拒绝同会话第二个活动动作，返回 `409 ACTION_CONVERSATION_IN_PROGRESS`；动作进入终态（确认 / 取消 / 过期 / 失败）后同会话才可再发起新申请。
- Trigger 只允许业务状态变化信号进入 Extractor：`action_proposal` 或 Memory-eligible Tool 成功调用（白名单由 `MemoryTaskTypePolicy` / `MemoryCapabilityRegistry` 提供）。普通 RAG、Evaluation、余额和历史查询以及 Read Path 注入的 `memory_context` 都不触发；Agent 失败终态（`route=error` 或 `provider_error` / `invalid_decision` / `step_budget_exhausted`）直接短路，不进入 Extractor。历史 ACTIVE Memory 仍由 Java Read Path 注入 Planner（不受 Trigger 收敛影响）；Trigger 与 Planner Context 的关注点解耦。

**应用过载响应**（HTTP 429，包含 `Retry-After: 1`）
```json
{
  "answer": "当前请求较多，请稍后重试。",
  "route": "busy",
  "safe": true,
  "category": "overloaded",
  "reason": "",
  "sources": [],
  "success": false,
  "traceId": "xxx"
}
```

**未完成执行恢复冲突**（Planner-first latest Checkpoint 不允许自动 Resume，HTTP 409）
```json
{
  "answer": "当前会话存在未完成的 Agent 执行，请重试原请求或重新开始会话。",
  "route": "error",
  "safe": true,
  "category": "recovery_conflict",
  "reason": "",
  "sources": [],
  "success": false,
  "traceId": "xxx"
}
```

该响应覆盖 exact request 不匹配、business date 改变、employee scope 改变、旧/不兼容 marker、interrupt、未知或多个 pending node、非 replay-safe Tool，以及当前权限已撤销但 Checkpoint 已物化对应 eval 或 business proposal 结果的情况。普通 Provider/Tool handled error 仍按原有 Agent error / 502 语义处理；完成的 Checkpoint 下一次相同请求仍 Fresh。若 latest Checkpoint 正在等待业务确认，普通 Chat 只返回原 Proposal/wait 状态，不重新规划；用户决定只能由 Java 事务提交后调用下述内部 resume endpoint。

**适用场景**：需要安全边界的知识库问答，支持自动区分 RAG 问答、评估查询和安全拒答。

---

### POST /api/agent/actions/{actionId}/confirm

确认并确定性执行一份 PostgreSQL 持久化的模拟年假草稿。Feature Flag 默认关闭。

请求 Header：`Authorization: Bearer <access-token>`、`X-Admin-Token: <admin-token>`、`Idempotency-Key: <UUID>`。仅在受控 Demo fallback 且没有 Bearer 时，才可用 `X-Demo-User-Id: <demo-user-id>` 代替登录身份。请求体只能包含：

```json
{"confirmationNonce": "<confirmation-nonce>"}
```

成功返回 `SUCCEEDED`、唯一 `requestId`、`originTraceId` 和本次确认 `traceId`。成功终态、首个幂等键和执行结果均持久化；Java/PostgreSQL 重启后，相同或不同合法幂等键重试仍返回原 `requestId`，并设置 `replayed=true`，不会重复扣减余额或创建 LeaveRequest。

前端首次 Confirm 使用 `crypto.randomUUID()` 生成并缓存该草稿的 Key。网络失败、HTTP 502/503 或其他可重试错误后，“重试确认”必须复用同一个 Key；快速双击由请求前同步锁拦截，只允许一个在途请求。请求体不会回传 summary、日期、原因、余额或状态。

首次成功示意：

```json
{
  "actionId": "...",
  "type": "ANNUAL_LEAVE_REQUEST",
  "status": "SUCCEEDED",
  "requestId": "...",
  "message": "模拟年假申请已提交。",
  "replayed": false,
  "completedAt": "...",
  "originTraceId": "...",
  "traceId": "..."
}
```

### POST /api/agent/actions/{actionId}/cancel

取消尚未确认的草稿。请求 Header 为 `Authorization: Bearer <access-token>` 和 `X-Admin-Token`；仅在受控 Demo fallback 且没有 Bearer 时使用 `X-Demo-User-Id`。不得携带 `Idempotency-Key`，请求体同样只能包含 `confirmationNonce`。重复取消返回 `CANCELLED` 且 `replayed=true`。前端取消成功后清理页面内存中的 nonce 和幂等 Key，并隐藏所有操作按钮。

Confirm/Cancel 都会在锁定 Action 后先校验员工归属，再检查 nonce、过期和状态。其他身份即使持有正确 actionId、nonce、Admin Token 和幂等键，也只得到与不存在 Action 完全相同的 `404 ACTION_NOT_FOUND`，且不会改变草稿、余额或申请记录。

React 根据 `expiresAt` 设置有界计时器，本地到期后禁用 Confirm/Cancel 并提示重新生成草稿；如果服务端返回 `ACTION_EXPIRED`，同样进入不可重试的过期终态。服务端时间与状态始终是权威来源。

后端只持久化 confirmation nonce 的 SHA-256 摘要，不保存明文。明文只存在当前页面内存，因此浏览器刷新后无法恢复旧草稿的确认凭据，需要重新生成草稿。

所有 PendingAction、confirm、cancel 和 Action 错误响应均包含：

```http
Cache-Control: no-store
```

Action 错误使用独立契约，错误码包括：

```text
INVALID_REQUEST
INVALID_IDEMPOTENCY_KEY
ADMIN_REQUIRED
INVALID_CONFIRMATION_NONCE
ACTION_NOT_FOUND
ACTION_IN_PROGRESS
ACTION_STATE_CONFLICT
ACTION_STALE
ACTION_EXPIRED
ACTION_INTERNAL_ERROR
BUSINESS_RULE_VIOLATION
BUSINESS_ACTIONS_DISABLED
ACTION_CAPACITY_EXCEEDED
ACTION_CONVERSATION_IN_PROGRESS
DEMO_IDENTITY_REQUIRED
DEMO_IDENTITY_INVALID
DEMO_IDENTITY_DISABLED
```

该接口是 PostgreSQL Sandbox：不接真实 OA，不支持中国法定节假日和调休。

### GET /api/demo/identities

仅在 `demo.identity.enabled=true` 时返回三个固定演示身份的 `userId`、`displayName` 和 `role`，响应为 `Cache-Control: no-store`。不返回 employeeId、余额、申请、nonce、Action 或数据库主键。关闭时返回 `503 DEMO_IDENTITY_DISABLED`。

`X-Demo-User-Id` 仅是默认关闭的本地/受控兼容 fallback，不是登录凭证；任何公开生产环境都不得依赖它建立用户身份。

---

## Python AI Service API（端口 8000）

### GET /agent/health

Python AI 服务健康检查。

**响应**
```json
{
  "service": "agent-python",
  "status": "UP",
  "concurrency": {
    "maxConcurrent": 3,
    "active": 0,
    "available": 3,
    "rejected": 0,
    "queueTimeoutMs": 500
  }
}
```

---

### GET /agent/ready

Python 就绪检查，验证 Provider 必要配置、Chunks 与 FAISS 索引。全部就绪返回 HTTP 200 / `READY`；否则返回 HTTP 503 / `NOT_READY` 以及结构化 `checks`。容器健康检查使用该接口。

---

### POST /agent/chat

统一 RAG 问答接口（稳定主链路）。

请求和响应格式同 Java `POST /api/chat`。

---

### POST /agent/langgraph/chat

LangGraph Agent 问答接口。

请求格式同 Java `POST /api/agent/langgraph/chat`。该内部接口额外使用 Java 设置的 `X-Allow-Business-Actions`、`X-Business-Date`、`X-Conversation-Id` 和服务端填充的 `memoryContext` body；不读取 Admin Token。`X-Business-Date` 是 Java 权威业务日期，供 `leave_proposal_tool` 在 Planner-first 路径下使用。Python 可在响应中附带不含 owner/lifecycle 的 `memory_proposal`，由 Java 当前认证请求处理。

Python 内部响应可能在 `route=action` 时携带 `action_proposal`：

```json
{
  "route": "action",
  "category": "business_action",
  "action_proposal": {
    "action_type": "ANNUAL_LEAVE_REQUEST",
    "start_date": "2026-07-20",
    "end_date": "2026-07-20",
    "reason": "示例原因",
    "half_day": "NONE"
  },
  "missing_fields": []
}
```

缺字段时 `action_proposal=null`，`missing_fields` 按 `start_date`、`end_date`、`reason` 的固定顺序返回。`action_proposal` 是 Java/Python 内部契约，不能绕过 Java 权威校验直接执行。

### POST /agent/langgraph/hitl/resume

仅供 Java `BusinessActionHitlCoordinator` 的服务间调用，不是浏览器或公网 API。请求必须带 Java 根据可信身份和会话生成的 `X-Agent-Thread-Id`、当前 `X-Employee-Id`、`X-Allow-Business-Actions`、`X-Business-Date` 与 `X-Conversation-Id`，body 只能是严格的 Java-authoritative HITL decision：

```json
{
  "schema_version": 1,
  "wait_id": "wait_<sha256>",
  "execution_id": "ex_<id>",
  "decision": "CONFIRMED",
  "action_id": "...",
  "action_type": "ANNUAL_LEAVE_REQUEST",
  "action_status": "SUCCEEDED",
  "request_id": "...",
  "message": "模拟年假申请已提交。"
}
```

Python 只校验 latest Checkpoint 中的 wait、execution、actor scope、correlation 与合法 pending/finalize/completed 状态，然后用 `Command(resume=...)` 继续 `approval_node → finalize_node`；不运行 Planner、Tool 或 Memory proposal pipeline。`CANCELLED`、`EXPIRED`、`REJECTED` 分别对应 `CANCELLED`、`EXPIRED`、`FAILED`，完成的 HITL 执行重复调用为 no-op。对 Java 已持久化的 terminal business result，当前 `X-Allow-Business-Actions=false` 仍可完成 approval/finalize 收口；该值会继续注入 Runtime Context，防止意外重新进入 Planner/Tool。关联不匹配、actor scope 变化或不安全 checkpoint 返回稳定的 recovery conflict，不改变 Checkpoint。

---

## 测试样例

### 1. RAG 问答（本地）

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期：`success=true`，answer 包含病假材料清单。

### 1b. RAG 问答（公网）

```bash
curl -X POST https://copilot.jintianchi.cn/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期：`success=true`，answer 包含病假材料清单，`traceId` 存在。

### 2. Agent RAG 问答

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期：`route=rag`, `safe=true`, `sources` 有值。

### 3. 评估查询

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```

预期：`route=eval`, `safe=true`，answer 包含评估指标。

### 4. 安全拒答

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么伪造病假证明？"}'
```

预期：`route=refuse`, `safe=false`。

### 5. Python 停服降级

```bash
# 先停止 Python 服务，再调用 Java
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"测试"}'
```

预期：`success=false`，answer 为"当前 AI 服务暂时不可用"，traceId 仍然存在。

---

## traceId 全链路

> **信任边界：** 外部请求传入的 `X-Trace-Id` 不被信任。Java 入口（`TraceIdFilter`）统一生成服务端 traceId，格式为 UUID v4。客户端传入的非法格式（含控制字符、超长、非 UUID）会被丢弃并重新生成。Java → Python 通过 `X-Trace-Id` 请求头透传服务端生成的 traceId。

所有接口支持 `X-Trace-Id` 响应头返回：

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"上班时间是什么？"}'
```

- Java 入口统一生成 UUID 格式的 traceId，不信任客户端传入值
- Java → Python 通过 `X-Trace-Id` 请求头透传
- 响应头和响应体都包含 `X-Trace-Id` / `traceId`
- Java 日志和 Python 日志都带 traceId，便于全链路排查

---

## 评估用例字段说明

评估用例文件：`data/eval/rag_eval_cases.json`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 用例唯一标识，如 `leave_001` |
| question | string | 用户问题 |
| expected_sources | list[string] | 预期命中的文档来源文件名 |
| expected_keywords | list[string] | 检索结果应包含的关键词（Retrieval Eval 用） |
| expected_answer_keywords | list[string] | LLM 回答应包含的关键词（Generation Eval 用） |
| expected_answer_keyword_groups | list[list[string]] | 同义词组，组内 OR、组间 AND（可选） |
| answerable | bool | `true` = 有答案，`false` = 无答案负样本 |

### keyword_groups 同义词组机制

`expected_answer_keyword_groups` 用于支持合理同义表达：

```json
{
  "expected_answer_keyword_groups": [
    ["直接主管", "上级主管", "直属主管", "主管领导", "直属上级", "直属领导", "上级领导", "部门领导"]
  ]
}
```

- 组内关键词：OR 关系（命中任意一个即可）
- 组间：AND 关系（每组都必须命中至少一个）
- 兼容 `expected_answer_keyword_groups` 和 `expected_answer_keywords` 共存

### failure_type 分类

Generation Eval 结果中的 `failure_type` 字段：

| 值 | 说明 |
|---|---|
| `passed` | 通过 |
| `keyword_too_strict` | 关键词过严，模型回答合理但未命中预期词 |
| `generation_incomplete` | 模型没答全，遗漏关键信息 |
| `llm_flaky` | LLM 输出波动，retry 后通过 |
| `no_answer_leakage` | 无答案场景模型泄漏了编造内容 |

### 检索模式参数

| 参数 | 可选值 | 默认 | 说明 |
|---|---|---|---|
| `retrieval_mode` | `vector` / `hybrid` / `hybrid_rerank` | `hybrid` | 检索模式 |
| `rewrite_mode` | `none` / `rule` | `none`（本地）/ `rule`（公网） | 查询重写模式 |

> `hybrid_rerank` 是实验模式。公网部署（v0.3.2+）默认启用 `rewrite_mode=rule`。
