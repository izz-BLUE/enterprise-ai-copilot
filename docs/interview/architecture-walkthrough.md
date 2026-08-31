# Architecture Walkthrough

目标：用 5 分钟让面试官听懂“谁负责什么、状态在哪里、为什么不会让 LLM 直接写业务”。

## 1. 入口与分层（约 45 秒）

```text
Browser/React → Nginx → Java Spring Boot → Python FastAPI
                                      ├→ PostgreSQL business DB
                                      └→ Python Agent / RAG / Checkpoint
```

Java 是业务控制面：认证、owner、Admin gate、trace、超时、并发、PendingAction、ExpenseClaim、Memory lifecycle 和最终业务事务。Python 是 AI 数据面：Safety Guard、RAG、DeepSeek、Planner、Tool Executor 和 LangGraph。前端只持有展示和当前页面确认状态。

## 2. RAG（约 45 秒）

```text
Markdown knowledge base
  → chunks + metadata
  → BGE embedding → FAISS
  → character BM25
  → RRF → bounded context → DeepSeek → answer + sources
```

FAISS 负责语义召回，BM25 负责中文关键字精确匹配；分数尺度不一致，所以用 RRF 按排名融合。无证据时明确拒答。38 个固定 case 分开检查检索和生成，避免只看最终文本。

## 3. Agent graph（约 45 秒）

仓库部署默认是 Planner-first：

```text
safety → planner ⇄ tool_executor → finalize
```

Planner 输出严格的 Pydantic decision，但只拥有规划权；Tool Executor 根据可信 employee/capability、动态可见集合、预算和成功签名做第二次校验。最多 6 次 Planner decision、5 次 Tool execution。生产入口固定 Planner-first，legacy Router-first 仅作为测试/离线兼容图保留。

## 4. Java authority（约 45 秒）

模型输出的 Proposal 不等于授权：

```text
Proposal → Java validate → PENDING_CONFIRMATION
         → nonce + owner + TTL + idempotency
         → confirm → PROCESSING → SUCCEEDED/FAILED
```

取消和过期分别进入 CANCELLED/EXPIRED。nonce 明文只返回一次，数据库保存 digest；confirm 的重复请求重放原 requestId。真正的 LeaveRequest、ExpenseClaim、ExpenseItem 只能在 Java PostgreSQL 事务中创建。

## 5. Expense walkthrough（约 90 秒）

```text
MCP trip/invoice read
  → deterministic expense proposal
  → WAITING_USER
  → Java confirm-time revalidation
  → ExpenseClaim + ExpenseItem committed
  → WAITING_EXTERNAL
  → Mock OA PENDING
  → approve/reject
  → webhook correlation → Java authoritative GET
  → ExpenseClaim terminal → external resume → END
```

Confirm-time revalidation 不相信旧 Proposal 的 facts：它重新检查 trip ownership/status/current dates，精确检查 invoice ownership/valid/duplicate/amount/category，并由 Java 重算金额。Stale 时 Action FAILED、Memory ABANDONED、HITL REJECTED，不创建 ExpenseClaim；OA 不可用时保留 Pending 并返回 503。

这里有两个必须区分的 wait：`WAITING_USER` 等用户确认；`WAITING_EXTERNAL` 等 OA 决策。Mock OA 的 webhook 不带 status，Java 先验证 HMAC/timestamp，再 GET OA 权威状态。Java 终态 commit 后才 external resume，所以 Python 挂掉不会回滚业务终态。

## 6. Memory 与 Checkpoint（约 45 秒）

Memory key 是 `(user_id, conversation_id)`，Java 只读 ACTIVE。Python 的 trigger→extractor→write policy 只返回 `UPSERT + ACTIVE` 提案；现有 ACTIVE Memory 本身不触发，纯 RAG 或 read-only Tool 也不触发。Java 负责 terminal lifecycle。

```text
tool_history       = 本次 execution 的已执行 Tool
execution_history  = 有界成功摘要，CONTEXT_ONLY
Checkpoint         = execution scene / interrupt / recovery
Java PostgreSQL    = business truth and Memory lifecycle authority
```

POSTGRES Checkpoint 模式使用 `PostgresSaver`。精确的同问题、同日期、同 actor scope 且 replay-safe 的 crash recovery 用 `graph.invoke(None)`；用户确认和外部审批分别用 `Command(resume)`。普通 Chat 不跨 active wait。

## 7. 部署与限制（约 25 秒）

生产 Compose 默认 Planner-first + PostgreSQL Checkpoint；Python 没有宿主机端口。当前仍是小规格单机：Java/Python guard 只在进程内生效，没有 distributed lock、Temporal/DBOS/Kafka、真实 OA 分布式事务或生产 SLA。Mock OA 和 fixture-backed MCP 只用于验证外部状态语义。

## 8. 收尾句

> 这个项目的核心不是让模型“更自由”，而是让模型只能在程序提供的 capability 内规划；所有跨服务、跨时间、跨系统的状态都由明确的 Java owner、correlation、幂等和失败语义收口。
