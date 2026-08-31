# Local Demo Guide

本手册按当前实现准备本地演示。推荐主线是差旅报销外部审批闭环；年假申请作为较短的第二条受控动作。所有演示都应使用本地或专用 Demo 数据，不要把 Demo header、共享 token 或 Mock OA 当作生产认证方案。

## 1. Demo scope

主线展示：

```text
RAG/Planner
  → Enterprise OA MCP read-only facts
  → expense Proposal
  → WAITING_USER + Java PendingAction
  → confirm-time revalidation
  → ExpenseClaim persisted
  → WAITING_EXTERNAL + Mock OA
  → signed webhook / reconciliation
  → authoritative status
  → external resume → Graph END
```

次线展示：`leave_proposal_tool → PendingAction → confirm/cancel → LeaveRequest`。

## 2. Prerequisites

- Java 17、Maven wrapper；
- Python 3.11、uv；
- Node 20、npm；
- Docker Compose 和可用 PostgreSQL；
- DeepSeek API key；
- Enterprise OA MCP fixture/service（默认 URL `http://127.0.0.1:8100/mcp`）；
- 如果演示 durable HITL/external approval：独立 LangGraph checkpoint PostgreSQL DSN。

启动基础设施：

```bash
docker compose -f deploy/docker-compose.local.yml up -d postgres mock-oa
```

复制 `agent-python/.env.example` 为 `.env`，并按演示目的配置：

```text
LANGGRAPH_CHECKPOINT_DSN=postgresql://<user>:<password>@localhost:5432/<db>
ENTERPRISE_OA_MCP_URL=http://127.0.0.1:8100/mcp
MEMORY_WRITE_MODE=DISABLED
```

Java Demo 环境还需要有效的 `AUTH_JWT_SECRET`、数据库配置、`DEMO_AUTH_ENABLED=true` 和 `BUSINESS_ACTIONS_ENABLED=true`。固定账号的密码边界如下：

```text
DEMO_PUBLIC_PASSWORD=demo-public-2026       # 刻意公开；与前端 VITE_PUBLIC_DEMO_PASSWORD 一致
DEMO_INTERVIEW_PASSWORD=<server-side-only>  # zhangsan；不要写入前端或公开文档
DEMO_ADMIN_PASSWORD=<server-side-only>      # admin；不要写入前端或公开文档
DEMO_AUTH_DEFAULT_PASSWORD=<server-side-only> # 仅 lisi/wangwu legacy seed
```

前端可复制 `frontend/.env.example`；其中 `VITE_PUBLIC_DEMO_USERNAME` / `VITE_PUBLIC_DEMO_PASSWORD` 会进入浏览器构建产物，只能填写公开 demo 凭据。`demo`（U10000/E10000）保留普通 Agent/RAG 与安全只读能力，即使 `BUSINESS_ACTIONS_ENABLED=true` 也不会获得业务写能力；`zhangsan`（U10001/E10001）继续按 Java trusted identity policy 演示 Leave/Expense，浏览器不需要 Admin Token。若启用外部审批，再配置 `MOCK_OA_ENABLED=true`、`MOCK_OA_BASE_URL=http://localhost:8010`、`MOCK_OA_WEBHOOK_SECRET`。外部审批 retry/reconciliation worker 会按间隔和批量参数低频运行，provider 关闭时 gateway fail-closed。

## 3. Start services

```bash
# Terminal 1: Python
cd agent-python
uv sync --group dev
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Java
cd backend-java
./mvnw spring-boot:run

# Terminal 3: Frontend
cd frontend
npm ci
npm run dev
```

先检查：

```bash
curl http://localhost:8080/api/ready
curl http://localhost:8080/api/agent/ready
curl http://localhost:8000/agent/ready
```

浏览器打开 `http://localhost:5173`。普通用户请求走 Java；不要把浏览器请求直接改到 Python 8000。

## 4. Primary demo: expense approval

### Step A — Prepare source facts

确保 Enterprise OA MCP fixture 中存在当前 Demo employee 可访问的 `APPROVED` trip 和有效、未重复的 invoices。需要检查的事实包括 trip ID、日期、cost center、invoice ID、金额和 category。演示前先确认 fixture 与 employee scope 一致。

### Step B — Create a proposal

在 Agent 模式输入类似：

```text
请根据我最近一次已批准出差和对应发票，准备一份差旅报销。
```

预期：

- Planner 调用 travel/invoice read tools，必要时调用 RAG 读取政策；
- expense proposal 使用成功 observations 和确定性金额计算；
- 返回 `pendingAction`/确认卡，或因缺字段返回 Clarification；
- 此时没有 ExpenseClaim、没有 Mock OA request、没有外部副作用。

话术：

> AI 只负责把已读事实整理成 Proposal。Proposal 不是授权，Java 还会做一次权威校验并生成一次性 nonce。

### Step C — Confirm

点击确认。前端只在页面内存保存 nonce 和幂等 key；Java 会：

1. 验证 owner、nonce、TTL、Action state 和 UUID idempotency key；
2. 调用 Python narrow adapter 做当前 trip/invoice revalidation；
3. 重新计算 reimbursable amount；
4. 在 PostgreSQL 事务内写 ExpenseClaim/ExpenseItem，并把 BusinessAction 置为 `SUCCEEDED`；
5. 事务提交后再恢复 Python HITL Checkpoint。

预期：确认成功不会直接把 OA 状态当作已批准；页面进入等待外部审批语义。

### Step D — Show WAITING_EXTERNAL

说明两个等待点的区别：

- `WAITING_USER`：等用户确认 Java PendingAction；
- `WAITING_EXTERNAL`：Java ExpenseClaim 已经写入，等 Mock OA 决定。

普通 Chat 不能穿过 `WAITING_EXTERNAL` 重新规划同一个 execution。

### Step E — Submit and decide in Mock OA

D2 公网 Demo 由 `admin` 登录 Copilot 后打开“模拟 OA 审批”页面完成审批。页面只调用 Java `/api/admin/mock-oa/**`，不会直接访问 Mock OA；下面的 Mock OA 管理端点仅保留给本地服务间调试使用。

确认 Java 已启用 external submission 后，Mock OA 会收到：

```text
POST http://localhost:8010/api/expense-approvals
Idempotency-Key: expense:<expenseId>
```

初始响应为 `PENDING`。使用 Mock OA 管理端点批准或拒绝：

```bash
curl -X POST http://localhost:8010/api/admin/expense-approvals/<requestId>/approve
# 或
curl -X POST http://localhost:8010/api/admin/expense-approvals/<requestId>/reject
```

Mock OA 先提交自己的 SQLite 终态，再 best-effort 发送 webhook。webhook 只有 event/request correlation，没有 status。

### Step F — Verify Java authority and resume

Java 收到通知后检查 HMAC 和 300 秒 timestamp window，然后 GET：

```text
GET http://localhost:8010/api/expense-approvals/<requestId>
```

只有 GET 的 `APPROVED/REJECTED` 能更新本地 ExpenseClaim。Webhook 丢失时，reconciliation worker 会按低频、限批策略自动等待 due poll；它只扫描 `WAITING_APPROVAL + MOCK_OA + external_request_id`，先做 CAS 再在事务外 GET。

终态提交后 Java 才调用 Python external resume。成功时 Graph 以 `Command(resume)` 到 END；Python 不重新跑 Planner/Tool，也不触发 Memory proposal pipeline。若 resume 失败，ExpenseClaim 终态仍保留，retry markers 支持重新投递。

## 5. Secondary demo: annual leave

在 Agent 模式输入包含明确日期、原因和申请意图的请求，例如：

```text
我想在下周一到下周二请年假，原因是个人安排。
```

预期流程：

```text
leave_proposal_tool
  → action_proposal 或 missing_fields
  → Java PendingAction PENDING_CONFIRMATION
  → Confirm / Cancel
  → LeaveRequest + balance transaction
```

展示重点：Proposal 阶段不扣余额；Confirm 做 owner/nonce/TTL/幂等/业务规则校验；重复 Confirm 重放同一 requestId；Cancel 或过期不会写 LeaveRequest。当前 Demo 不处理法定节假日和调休。

## 6. Safety and RAG fallback

### 普通 RAG

```bash
curl -X POST http://localhost:8080/api/chat `
  -H 'Content-Type: application/json' `
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期有 `answer`、`sources`、`traceId`。知识库没有证据时回答明确拒答，不编造。

### Safety Guard

在 Agent 模式输入“怎么伪造病假证明？”。预期 `safe=false` 或 `route=refuse`，不进入 Planner/RAG。说明 Safety Guard Lite 是规则型纵深防御，不是完整授权或内容安全系统。

### Eval gate

评估与管理员日志能力由 Java 已验证 JWT 的 `role=ADMIN` 授权；EMPLOYEE 不能访问。浏览器不提供或发送 Admin Token。`ADMIN_TOKEN` 如配置，仅作为 `BUSINESS_ACTIONS_REQUIRE_ADMIN=true` 时内部业务动作的 server-side hardening，Python 只消费 Java 的 `allow_eval` 结果。

## 7. Troubleshooting

| 现象 | 检查 |
|---|---|
| Agent 返回 checkpoint unavailable | DSN、PostgreSQL health、`PostgresSaver.setup()` |
| Proposal 缺少事实 | `ENTERPRISE_OA_MCP_URL`、fixture employee ownership、trip/invoice 状态 |
| Confirm 返回 503 | Python revalidation adapter 或 OA MCP 不可用；PendingAction 应保持可重试 |
| Confirm 被拒绝为 stale | trip/invoice 在 Proposal 后发生变化；重新读取当前事实再建 Proposal |
| OA 状态不变化 | `MOCK_OA_ENABLED`、base URL、webhook secret、Mock OA SQLite volume |
| webhook 被拒绝 | raw body 签名、timestamp、精确 path 和共享 secret |
| external resume 没有立即收口 | Java 终态是否已提交、retry markers；不要回滚 ExpenseClaim |
| 两次请求互相 busy | 同一 runtime thread 的 process-local guard 正在保护完整 lifecycle |

## 8. Demo boundary

演示结束后可关闭 `BUSINESS_ACTIONS_ENABLED`、`MOCK_OA_ENABLED` 和 Memory write。不要把真实 token、nonce、cookie、raw webhook 或用户数据写入截图和日志。
