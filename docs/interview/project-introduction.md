# Project Introduction for Interviews

以下口径以当前仓库实现和最终验证基线为准。描述项目时强调边界：这是可演示、可恢复、可验证的小规格系统，不把它包装成生产级平台。

## 30 seconds

> 我做了一个企业 AI Copilot，解决企业制度问答和受控业务流程辅助问题。Java Spring Boot 负责认证、权限和业务状态，Python FastAPI 负责 RAG、LLM 和 LangGraph Agent，前端用 React。RAG 用 FAISS + 字符级 BM25 + RRF，并返回来源；Agent 可以读取差旅和发票事实生成报销 Proposal，但不直接写库，必须经过 Java PendingAction、用户确认、confirm-time revalidation 和外部审批。项目还验证了 PostgreSQL Checkpoint、HITL、外部 resume 以及 Memory 与业务事实的边界。

## 1 minute

> 这个项目不是简单的“问答 API”。稳定链路是 Java → Python → hybrid retrieval → DeepSeek；Agent 链路默认使用 Planner-first LangGraph，Planner 只负责有限规划，Tool Executor 负责 capability、身份、预算和去重，Java 是最终业务 authority。
>
> 我用差旅报销作为主要场景：Agent 通过 Enterprise OA MCP 读取当前 trip/invoice，程序代码确定性计算金额并生成 Proposal；用户确认后，Java 在本地事务中写 ExpenseClaim/ExpenseItem，再进入独立的 WAITING_EXTERNAL，Mock OA 通过 authoritative GET 决定批准或拒绝，Java 最后恢复 Python Graph 到 END。Memory 只保存同一 user/conversation 的 ACTIVE 任务连续性，不能替代权限、当前业务事实或 Checkpoint。项目有 1402 个 Python 通过、34 个预期 skip，PostgreSQL durable flows 34/0，Java 334、MCP 24、Mock OA 17、前端 44 的接受基线。

## 3 minutes

### 背景

企业员工需要查询 HR、银行和 IT 制度，也会提出请假、差旅报销等需要确认的业务请求。单纯把自然语言直接交给 LLM 会产生两个问题：知识答案缺少可追溯性，业务写操作缺少权限和幂等边界。

### 架构

Java 是 gateway 和 control plane：解析 JWT/受控身份、生成 traceId、决定 capability、保存 PendingAction/业务结果/Memory lifecycle，并控制对 Python 的调用。Python 是 AI data plane：做 Safety Guard、hybrid RAG、DeepSeek 调用、Planner、Tool Executor 和 LangGraph checkpoint。Enterprise OA MCP 只读差旅和发票，Mock OA 独立模拟外部审批。React 只负责交互，不拥有业务权限。

### Agent 与 RAG

生产入口固定使用 `safety → planner ⇄ tool_executor → finalize`；legacy Router-first 仅作为测试/离线兼容图保留。Planner 最多 6 次 decision，Tool 最多 5 次执行，Tool 可见性由 Java trusted context 和服务配置动态收缩。RAG 先用 BGE embedding、FAISS 和字符 BM25 召回，再用 RRF 融合，生产固定不做 Rewrite，`rule` 仅用于离线对照；38 个 case 评估 source/keyword hit、生成关键词和 no-answer refusal。

### 报销业务闭环

```text
MCP facts → deterministic Proposal → WAITING_USER
→ Java PendingAction → Confirm-time revalidation
→ ExpenseClaim/Item transaction
→ WAITING_EXTERNAL → Mock OA PENDING
→ webhook notification + authoritative GET
→ APPROVED/REJECTED → external resume → Graph END
```

用户确认前不产生业务副作用。Confirm-time revalidation 发现 trip/invoice/amount stale 时 fail closed：Action FAILED、Memory ABANDONED、HITL REJECTED，不创建 ExpenseClaim；OA 不可用则保留 Pending 并允许重试。Java 终态提交先于 Python external resume，resume 失败不回滚业务事实。

### Memory 与恢复

Memory key 是 `(user_id, conversation_id)`，只读 ACTIVE。当前 ACTIVE Memory 不会单独触发 Extractor，只有 action proposal 或白名单 Tool 成功才触发 Python trigger→extractor→`UPSERT + ACTIVE` proposal，Java 才落库。`tool_history` 是当前 execution，`execution_history` 是有界 `CONTEXT_ONLY`，Checkpoint 是执行现场，Java DB 是业务事实。POSTGRES 模式用 `PostgresSaver`；精确匹配的 crash recovery 用 `graph.invoke(None)`，两个 wait 各自用 `Command(resume)`。

## Resume bullets

- 设计 Java Spring Boot + Python FastAPI 双服务 AI 平台，将 Java 认证/权限/业务事务与 Python RAG/LLM/LangGraph 编排解耦，并通过 trusted runtime context 防止 LLM 伪造身份和能力。
- 构建 FAISS + 字符级 BM25 + RRF hybrid retrieval 与 38-case 回归评估，覆盖 source hit、keyword hit、生成关键词、no-answer refusal 和 baseline regression。
- 实现受控报销链路：Enterprise OA MCP 只读事实 → 确定性 Proposal → Java PendingAction/nonce/幂等 → confirm-time revalidation → ExpenseClaim → Mock OA authoritative approval。
- 实现 PostgreSQL-backed LangGraph Checkpoint、`WAITING_USER`/`WAITING_EXTERNAL` 双 interrupt、精确 correlation、crash resume、webhook/reconciliation 和终态后的 external resume。
- 建立 Scoped Conversation Memory 边界：Java 认证作用域与生命周期权威、Python 仅返回 `UPSERT + ACTIVE` 提案，并将 Memory、tool history、execution history、Checkpoint 与业务 DB 分离。

## 面试官第一印象

这是一个把 RAG/Agent、业务 authority、HITL、外部一致性和可验证边界放在同一个小型工程里的项目；亮点是系统语义清楚，短板是生产化规模和真实 OA 集成仍未完成。

## 三个亮点

1. AI 只计划，Java 才授权和写入，避免“模型直接执行数据库操作”。
2. Expense workflow 把用户确认和外部审批拆成两个可恢复 wait，并明确 webhook 不是真实状态源。
3. 质量证据分层：Java/Python/前端/MCP/Mock OA/Checkpoint 各有独立基线，不用单一“Demo 能跑”替代验证。

## 三个短板

1. 单机 process-local guard，不支持多实例分布式协调。
2. Mock OA 和 fixture-backed MCP 不能替代真实生产 OA、凭据、provider-side version/CAS、可靠 after-commit delivery（如需要可评估 Outbox）和 SLA 验证。
3. 规则 Safety Guard、38-case eval 和小规格容量基线仍不足以证明通用生产安全性。
