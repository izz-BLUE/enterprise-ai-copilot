# CLAUDE.md

本文件用于指导 Claude Code（claude.ai/code）在本仓库中进行代码工作。

## 项目概述

企业级 RAG + Agent 业务流程辅助平台，采用 Java + Python 双服务架构：
- **Java Spring Boot**: 企业业务主系统（用户权限、知识库管理、审计日志、业务流程）
- **Python FastAPI**: AI Agent 服务（RAG、LangChain/LangGraph、Tool Calling、Prompt 编排）
- **React + Vite**: 前端界面

## 常用命令

### 启动开发环境（三个终端）

```bash
# Terminal 1: Python AI Service
cd agent-python && uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Java Backend
cd backend-java && ./mvnw spring-boot:run

# Terminal 3: Frontend
cd frontend && npm run dev
```

### 测试

```bash
# Python 测试
cd agent-python && uv run pytest

# Java 测试（包含 Testcontainers 集成测试）
cd backend-java && ./mvnw test

# Frontend E2E 测试
cd frontend && npm run test:e2e
```

### 构建与部署

```bash
# Docker Compose 生产部署
cd deploy && docker compose -f docker-compose.prod.yml up -d
```

## 架构要点

### 请求链路

```
前端 → Java (8080) → Python (8000)
      ↓
  /api/chat              → /agent/chat (普通 RAG)
  /api/agent/langgraph/chat → /agent/langgraph/chat (LangGraph Agent)
  /api/agent/actions/{id}/confirm → Business Action 确认
```

### D2 Mock OA 管理边界

管理员审批台只调用 Java `/api/admin/mock-oa/**`，由已验证 JWT 的 `role=ADMIN` 授权；Java 服务端调用内网 Mock OA，浏览器不接触 Mock OA secret、`ADMIN_TOKEN` 或 `X-Admin-Token`。Mock OA 终态与 Java Expense 终态保持独立，直到 webhook 或 reconciliation 成功收口。

### Phoenix 可观测性

- `PHOENIX_TRACING=false` 为默认值；Phoenix 是可选旁路，不改变 Java → Python API 契约。
- 启用时统一使用 OpenTelemetry/OpenInference 自动插桩与 BatchSpanProcessor；初始化、导出和关闭失败不得阻断业务。
- 默认不采集 Prompt、用户输入、检索正文和模型输出；`business_trace_id` 只做 Trace 关联，不参与身份、权限或业务决策。
- Phoenix 不替代仓库内 deterministic retrieval/generation/agent eval；自托管控制台通过 Compose `observability` profile 按需启动。

### Python Agent 状态图（LangGraph）

main 的生产入口固定使用 Planner-first 状态图；legacy 图仅保留给直接测试/离线兼容场景：

- **legacy Router-first**（仅直接测试/离线兼容）

```
START → safety_node → router_node → rag_node → END
                              ├→ eval_node → END
                              ├→ action_node → END
                              └→ refuse_node → END
```

- **Planner-first**（生产唯一入口）

```
START → safety_node → planner_node ⇄ tool_executor_node → finalize_node → END
```

Planner-first 的 Tool Catalog 注册 RAG、评估、Java 只读、Enterprise OA 只读和受控 Proposal 能力；实际可见集合由程序层按权限与配置动态收缩，**模型不能自行扩大 Tool 权限**：

- 始终可见：`rag_answer_tool`
- 受信任 `employee_id`、`JAVA_BASE_URL`、`JAVA_INTERNAL_TOKEN` 均非空时追加：`leave_balance_tool` / `leave_request_tool`
- `allow_eval=true` 时追加：`eval_report_tool`
- `allow_business_actions=true` 且受信任 `employee_id` 非空时追加：`leave_proposal_tool`；公开 `demo` 身份由 Java 固定为 `allow_business_actions=false`

Capability Gate 只决定 Planner 当前应该看见哪些 Tool，不是最终授权边界；Executor、Tool 与 Java 仍保留各自的身份、权限、参数、预算和业务授权校验。`business_date` 不属于 Capability Gate。Planner 拥有规划权但没有最终业务执行授权；可信系统字段（`employee_id` / `business_date` / `trace_id`）由程序层注入，不进入 LLM `arguments`。`leave_proposal_tool` 只生成 Proposal / Clarification，不执行写操作。

### LangGraph PostgreSQL 执行快照

- Java 仅用已认证 `VerifiedIdentity.userId()` 和已解析 `conversationId` 计算稳定 `X-Agent-Thread-Id`；客户端 header 不可信。
- Python 在 FastAPI 启动阶段固定创建 PostgreSQL Pool、执行 `PostgresSaver.setup()`、编译持久化图；DSN 缺失或任一步失败即启动失败，不降级。
- Planner 与 legacy 分别使用 `:planner-v1` / `:deterministic-v1` 后缀；每个节点 `durability="sync"` 写入。Planner-first fresh execution 保存 strict recovery marker；同一 thread 的 exact same unfinished request 通过 latest `snapshot.next`、marker/date/pending-node/replay-safe 校验后使用 `graph.invoke(None)` 恢复，并重新注入当前 Runtime Context。completed execution 和 legacy deterministic graph 永远 Fresh；interrupt、冲突、不兼容 marker 或 unsafe Tool fail-closed 为 409。
- Checkpoint 只保存 Agent 执行状态；Conversation Memory 仍是语义连续性，Java 业务数据库仍是业务事实和授权权威。`tool_history` 是当前 execution 历史，Fresh 时清空、Resume 时保留；`execution_history` 是最多 16 条的 `CONTEXT_ONLY` 成功步骤摘要，仅在 ACTIVE Memory 且 task type 匹配时 hydrate，不可直接复用为当前业务事实。Recovery marker 只保存 request/date/actor scope locator，不保存 raw employee 或权限；当前权限撤销且旧状态已有 eval 成功结果或 business proposal material 时 fail-closed。
- P3-5A / P3-5B2a / P3-5B2b / P3-5B3：Python 图只为已由 Java 成功提交且带本地 ExpenseClaim `request_id` 的报销确认追加持久化 `external_wait_node(interrupt)`。B2a 的 Mock OA SQLite 先提交 PENDING → APPROVED/REJECTED，再发送不含 status 的 HMAC-SHA256 webhook；Java 验证原始 body、5 分钟 timestamp 后 GET OA 权威状态，以幂等且禁止回退的方式更新 ExpenseClaim。B2b 由 Java reconciliation worker 始终低频、限批地处理 due 的 `WAITING_APPROVAL + MOCK_OA` 记录，先做 `external_last_checked_at` CAS，再执行权威 GET；provider 关闭或查询失败时 fail-closed，Webhook 与 reconciliation 共用状态同步逻辑。B3 只在 Java ExpenseClaim 终态提交后从持久化 correlation 重建可信 Runtime Context，使用 `allow_eval=false`、`allow_business_actions=false` 调用 external resume；Python 严格匹配后用 `Command(resume)` 收口 Graph END。普通 Chat 不跨过 external interrupt，Python 失败不回滚 Java 终态，交付可重试且同 payload 幂等；当前 Java thread guard 仍是单实例进程内边界。

- **safety_node**: Safety Guard Lite —— 启发式纵深防御过滤器（非授权/信任/权限边界）；NFKC+零宽字符+控制字符规范化，五族高置信规则（prompt_override / prompt_extraction / credential_extraction / tool_abuse / business_policy_bypass），明确攻击拦截、咨询放行，原始输入原样传给下游
- **router_node**（legacy Router-first）: 规则路由（eval 关键词 → eval，年假意图 → action，其他 → rag）
- **planner_node**（Planner-first）: 输出严格结构化的 PlannerDecision（Pydantic 严格白名单）；`MAX_PLANNER_STEPS=6`；最终决策可触发 `task_complete` / `refused` / `not_allowed` / `provider_error` / `invalid_decision` / `step_budget_exhausted`
- **tool_executor_node**（Planner-first）: 执行 Planner `action=tool` 决策；`MAX_TOOL_CALLS=5`；按结构 / 身份 / 权限 / WorkflowGuard / Tool 预算 / 成功签名去重顺序校验；任何执行前拦截不计数
- **rag_node**: Hybrid Retrieval (Faiss + BM25 + RRF) + LLM 生成
- **action_node**（legacy Router-first）: 年假申请受控业务动作确定性 Proposal 流程（不依赖 `JAVA_INTERNAL_TOKEN`）
- **eval_node**: 读取评估报告

### 检索模式

- **hybrid**（默认）: Faiss 语义检索 + BM25 + RRF 融合排序
- **vector**: Faiss + keyword 合并去重
- **hybrid_rerank**: Hybrid + Cross Encoder 精排（实验模式）
- 生产 Retrieval query 在进入上述检索器前使用 `normalize_retrieval_query()` 做窄范围语义等价规范化；`rewrite-mode=rule` 仅为 Legacy Experimental Rewrite 离线能力。

### 并发控制

Java 和 Python 都有并发限制：
- Java: `PythonAgentBulkhead`（Semaphore，默认 3 并发）
- Python: `ai_request_limiter`（默认 3 并发，500ms 队列超时）
- POSTGRES LangGraph thread：单 worker 进程内同一最终 thread 的 latest recovery inspection → Fresh/Resume Graph invoke → final Checkpoint 由 `active_thread_ids` 保护，忙时快速返回 `429 + Retry-After`；不同 thread 不互相阻塞。多 worker / 多实例的分布式 lease/lock 未实现。
- Java LangGraph conversation：`AgentRuntimeThreadExecutionGuard` 在 Memory Read 前保护 `Memory Read → Python → PendingAction/Memory persist → response`，同一 runtime thread 忙时快速返回 `429 + Retry-After`；多 Java 实例的分布式 lease/lock 未实现。

### Business Action 流程

年假申请的受控业务动作（Planner-first 路径）—— Python `leave_proposal_tool` 生成两种结果，分叉处理：

```text
- action_proposal（字段完整）
   → Java LangGraphAgentController
   → BusinessActionService.createPending
   → PendingAction（PostgreSQL 持久化，TTL 过期）
   → confirmationNonce 由 Java 生成（32 字节 SecureRandom，DB 仅存 SHA-256 摘要）

- missing_fields（Clarification）
   → Clarification response
   → 用户补充信息
   → 不创建 PendingAction（不进入 BusinessActionService / Confirm / Cancel）
```

只有已经创建 `PendingAction` 才进入人工确认链路：

1. 前端展示确认卡片，用户确认/取消；`confirmationNonce` 仅在页面内存保留，不写入 DOM / 日志 / 浏览器持久化存储
2. Java `BusinessActionController /confirm` 或 `/cancel` 执行确认（owner 校验、nonce 校验、状态机、TTL、幂等、余额扣减）
3. `/confirm` 成功后由 Java `LeaveExecutionGateway` 在 PostgreSQL 事务内执行最终写操作（`source_action_id` 唯一约束保证幂等）

`leave_proposal_tool` 不生成 `confirmationNonce`、不执行写操作，且不依赖 `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN`。

## 关键配置

### 环境变量（Python）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | LLM API Key（必填） | - |
| `DEEPSEEK_BASE_URL` | API 地址 | https://api.deepseek.com |
| `DEEPSEEK_MODEL` | 模型名称 | deepseek-chat |
| `EMBEDDING_BACKEND` | 推理后端 | torch (可选 onnx_direct) |
| `LANGGRAPH_CHECKPOINT_DSN` | PostgreSQL 执行快照 DSN | 必填 |
| `LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS` | PostgreSQL 连接/就绪超时 | 3 |
| `JAVA_BASE_URL` | Python → Java 内部只读端点地址 | 空（只读企业 Tool 关闭；空时返回 `LEAVE_READ_DISABLED`） |
| `JAVA_INTERNAL_TOKEN` | Python → Java 内部只读端点鉴权 | 空（空时鉴权失败） |
| `JAVA_TIMEOUT_SECONDS` | Python → Java 内部只读超时秒数 | 5 |

### 环境变量（Java）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_TOKEN` | server-only 业务动作 hardening Token；浏览器不接触 | 空 = 不启用额外 hardening |
| `BUSINESS_ACTIONS_ENABLED` | 启用业务动作 | false |
| `BUSINESS_ACTIONS_REQUIRE_ADMIN` | 额外要求内部请求提供 `ADMIN_TOKEN` | false |
| `SPRING_DATASOURCE_URL` | PostgreSQL URL | jdbc:postgresql://localhost:5432/enterprise_ai_copilot |

## 数据目录

- `data/hr/`, `data/bank/`, `data/it/`: 原始知识库文档（Markdown）
- `data/processed/`: 构建产物（chunks.json, faiss.index, faiss_metadata.json）
- `data/eval/`: 评估用例和报告（rag_eval_cases.json, reports/）

## 评估体系

- **检索评估**: source_hit_rate + keyword_hit_rate → final_pass_rate
- **生成评估**: keyword_groups 同义词组（组内 OR、组间 AND）
- **负样本**: 10 个 no-answer 用例验证拒答能力
- **RAG quality gate**: CI `python-eval` job 运行生产 Retrieval gate；运行时证据门控实验仍不作为生产运行时机制

## 注意事项

- Python 端 `DEEPSEEK_API_KEY` 未配置时，LLM 调用不可用，但检索评估仍可运行
- Faiss 索引和 metadata 在模块加载时初始化，文件不存在时仅警告不阻塞
- Embedding 模型首次 encode 时延迟加载
- 浏览器 eval/管理能力由 Java 从已验证 JWT 的 `role=ADMIN` 授权；`ADMIN_TOKEN` 不进入前端。`BUSINESS_ACTIONS_REQUIRE_ADMIN=true` 时仅对业务动作增加 server-side token hardening。
- Business Action 默认关闭，需设置 `BUSINESS_ACTIONS_ENABLED=true` 启用
