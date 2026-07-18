# 接口文档

## 公网演示地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 公网演示 | `https://copilot.jintianchi.cn` | React 前端 + Java API |
| 健康检查 | `https://copilot.jintianchi.cn/api/health` | Java 健康 |
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

### GET /api/health

Java 服务健康检查。

**响应**
```json
{
  "service": "backend-java",
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

### GET /api/agent/health

Java 代理 Python 健康检查。

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
  "success": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| model | string | 大模型名称 |
| traceId | string | 请求追踪 ID（全链路一致） |
| success | bool | 是否成功 |

> 注：当前版本 `/api/chat` 响应中不包含 `sources` 字段。RAG 引用来源仅在 Agent 链路（`/api/agent/langgraph/chat`）的 `sources` 字段返回。

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
{"message": "病假需要提供哪些材料？"}
```

**响应**（RAG 问答）
```json
{
  "answer": "根据企业知识库，病假需要提供...",
  "route": "rag",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": ["hr_leave_policy_real_sample_010", "hr_leave_policy_real_sample_026"],
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

Python 内部 Action 响应先产生 `action_proposal`，Java 会重新执行权限、日期、工作日、余额和冲突校验；校验通过后，公网响应只暴露可确认的 `pendingAction`：

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

该响应使用 `Cache-Control: no-store`。`traceId` 来自 Java 入口；它同时作为 PendingAction 的权威 `originTraceId`，不采用 Python 响应中的 traceId。

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
| route | string | 路由结果：`rag` / `eval` / `action` / `refuse` / `busy` / `error` |
| safe | bool | 安全守卫是否通过 |
| category | string | 安全分类：`normal` / `illegal_or_policy_violation` / `policy_bypass` / `cybersecurity_attack` / `audit_tampering` / `unauthorized_access` / `access_control` / `overloaded` / `error` |
| reason | string | 拒答原因（安全问题时）。异常场景下为空字符串，异常详情不返回给用户，仅记录在服务端日志中 |
| sources | list | RAG 引用来源 chunk ID 列表 |
| success | bool | 是否成功 |
| traceId | string | 请求追踪 ID |
| pendingAction | object/null | Java 权威校验后创建的待确认动作；仅完整 Action 返回 |

**权限行为（v0.3.2+）：**

- 普通 RAG 问答和 Safety Guard 拒答不受权限影响
- 公网部署（v0.3.2+）`ADMIN_TOKEN` 必须为非空值，Compose 启动时强制校验
- Evaluation 查询需要管理员权限：请求头 `X-Admin-Token` 必须匹配才允许 eval 路由
- 本地开发 `admin.token` 可为空（Demo 模式），所有用户均可访问 Evaluation
- `X-Admin-Token` 由调用方发送给 Java 后端，不应由前端 role 代替（权限判断在 Java 后端完成）
- `X-Allow-Eval` 是 Java → Python 内部 header，表示 Java 已完成权限判断，**不是认证凭证**，Python 不应将其当作独立安全边界

**无权限 Evaluation 响应**（`admin.token` 非空且未提供正确的 `X-Admin-Token`）
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

**适用场景**：需要安全边界的知识库问答，支持自动区分 RAG 问答、评估查询和安全拒答。

---

### POST /api/agent/actions/{actionId}/confirm

确认并确定性执行一份 Java 内存中的模拟年假草稿。Feature Flag 默认关闭。

请求 Header：`X-Admin-Token: <admin-token>`、`Idempotency-Key: <UUID>`。请求体只能包含：

```json
{"confirmationNonce": "<confirmation-nonce>"}
```

成功返回 `SUCCEEDED`、唯一 `requestId`、`originTraceId` 和本次确认 `traceId`。相同或不同幂等键在成功后重试均返回原 `requestId`，并设置 `replayed=true`，不会重复扣减余额。

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

取消尚未确认的草稿。请求 Header 为 `X-Admin-Token`，不得携带 `Idempotency-Key`，请求体同样只能包含 `confirmationNonce`。重复取消返回 `CANCELLED` 且 `replayed=true`。前端取消成功后清理页面内存中的 nonce 和幂等 Key，并隐藏所有操作按钮。

React 根据 `expiresAt` 设置有界计时器，本地到期后禁用 Confirm/Cancel 并提示重新生成草稿；如果服务端返回 `ACTION_EXPIRED`，同样进入不可重试的过期终态。服务端时间与状态始终是权威来源。

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
```

该接口是 Sandbox：不接真实 OA、不使用数据库，不支持中国法定节假日和调休。

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

### POST /agent/chat

手写 RAG 问答接口（稳定主链路）。

请求和响应格式同 Java `POST /api/chat`。

---

### POST /agent/langgraph/chat

LangGraph Agent 问答接口（实验链路）。

请求格式同 Java `POST /api/agent/langgraph/chat`。该内部接口额外使用 Java 设置的 `X-Allow-Business-Actions` 和 `X-Business-Date`，不读取 Admin Token。

完整 Action 的 Python 内部响应包含确定性 `action_proposal`：

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
