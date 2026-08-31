# Interview Demo Script

建议时长 8–10 分钟。主线演示差旅报销，年假只做第二个短例子；如果外部依赖不可用，直接切换到已录制的请求/响应和架构图，不声称真实 OA 已接入。

## 0. Opening（30 秒）

> 我先演示普通企业 RAG，再演示一个受控报销流程。重点不是让 LLM 直接操作数据库，而是展示 Planner、Tool、Java authority、用户确认、外部审批和 durable resume 如何协作。

## 1. RAG baseline（60 秒）

输入：`病假需要提供哪些材料？`

展示：

- Java `/api/chat` 作为稳定入口；
- Python 通过 FAISS + 字符 BM25 + RRF 检索；
- 返回 answer、sources、traceId；
- 知识库无证据时明确拒答。

话术：

> RAG 负责知识证据，不负责业务授权。检索和生成有独立评估，不能只凭 Demo 观感判断质量。

## 2. Safety and mode boundary（45 秒）

在 Agent 模式输入：`怎么伪造病假证明？`

预期 `safe=false` 或 `route=refuse`，说明 Safety Guard 位于 Planner/RAG 前，是规则型纵深防御，不是完整授权系统。

说明：生产入口固定走 Planner-first；Router-first 仅作为测试/离线兼容图保留。Planner 只有规划权，Tool Executor 和 Java authority 仍是执行边界。

## 3. Expense Proposal（90 秒）

输入：

```text
请根据我最近一次已批准出差和对应发票，准备一份差旅报销。
```

展示 Enterprise OA MCP 的 read-only travel/invoice facts 和政策 RAG。预期出现报销确认卡或明确的 missing fields。

关键话术：

> `expense_proposal_tool` 只消费成功的结构化 observations；金额、住宿上限和明细由程序确定性计算。这个阶段没有业务写入、没有扣款、没有 Mock OA request。

## 4. WAITING_USER and Confirm（90 秒）

展示确认卡并点击 Confirm。

```text
Proposal
  → PendingAction PENDING_CONFIRMATION
  → user confirm + nonce + idempotency key
  → Java confirm-time revalidation
  → ExpenseClaim + ExpenseItem transaction
```

讲清：nonce 由 Java 生成且数据库只存 digest；Java 验证 owner、TTL、状态和幂等。confirm-time adapter 重新检查 trip/invoice ownership、状态、当前日期、valid/duplicate/amount/category；stale 会 FAILED + ABANDONED + REJECTED，并通过 rejected resume 到 Graph END，不创建 claim；如果 Java stale 终态提交后 Python resume 暂时不可用，Java FAILED 保留，重复 Confirm 不重新查询 OA，只重试确定性的 REJECTED continuation；OA 不可用保留 Pending 并返回 503。

## 5. WAITING_EXTERNAL（60 秒）

说明：Java 事务提交成功后，Python 用 `Command(resume)` 收口用户确认，并进入另一个 durable interrupt：

```text
WAITING_USER     = 等用户确认 PendingAction
WAITING_EXTERNAL = 等外部 OA 决定 ExpenseClaim
```

普通 Chat 不能跨过 active external wait，也不会重新跑 Planner。

## 6. Mock OA decision（90 秒）

在 Mock OA 管理端点执行 approve 或 reject：

```bash
curl -X POST http://localhost:8010/api/admin/expense-approvals/<requestId>/approve
```

说明 Mock OA SQLite 先提交终态，再 best-effort 发送不带 status 的 HMAC webhook。Java 验证原始 body signature 和 300 秒 timestamp，再 GET Mock OA 权威状态：

```text
GET /api/expense-approvals/{requestId}
```

同终态 no-op，禁止回退和反向覆盖。Webhook 丢失时，始终运行的 bounded reconciliation worker 共享同一 status-sync service，provider 关闭时 fail-closed。

## 7. External resume（60 秒）

展示 `ExpenseClaim APPROVED/REJECTED` 后的 Python resume 或最终 response。

> Java 终态先提交，再发 external resume。resume 失败不会回滚 Java 业务事实；`external_resume_*` markers 支持重试，Python 会严格校验 wait/execution/action/request correlation。

## 8. Annual leave secondary example（45 秒）

输入：`我想在下周一到下周二请年假，原因是个人安排。`

```text
leave_proposal_tool
  → PendingAction
  → confirm/cancel
  → LeaveRequest + balance transaction
```

强调 Proposal 不扣余额，Confirm 才触发 Java 事务；重复 Confirm 重放 requestId，Cancel/Expire 不写 LeaveRequest。

## 9. Closing（30 秒）

> 项目当前是小规格单机和短时受控验证，不承诺生产 SLA。它的工程亮点是明确的 authority boundary、可恢复的双 wait、外部状态的 authoritative GET，以及 Memory、execution history、Checkpoint 和业务 DB 的分层；真正生产化还需要正式身份、真实 OA 的 provider-side version/CAS/幂等契约；如需可靠的 after-commit command/event delivery，再评估 Transactional Outbox；此外还需要分布式协调、完整观测和容量基线。
