# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

### Python Agent 状态图（LangGraph）

main 同时保留两套互斥状态图，由 `AGENT_LOOP_ENABLED` 切换：

- **legacy Router-first**（`AGENT_LOOP_ENABLED=false`，仓库部署默认）

```
START → safety_node → router_node → rag_node → END
                              ├→ eval_node → END
                              ├→ action_node → END
                              └→ refuse_node → END
```

- **Planner-first**（`AGENT_LOOP_ENABLED=true`，需显式开启）

```
START → safety_node → planner_node ⇄ tool_executor_node → END
```

Planner-first 最多支持 5 个 Tool，实际可见集合由程序层按权限动态收缩，**模型不能自行扩大 Tool 权限**：

- 默认可见：`rag_answer_tool` / `leave_balance_tool` / `leave_request_tool`
- `allow_eval=true` 时追加：`eval_report_tool`
- `allow_business_actions=true` 时追加：`leave_proposal_tool`

Planner 拥有规划权但没有最终业务执行授权；Tool Executor 独立做权限 / Tool 预算 / 成功签名去重校验；可信系统字段（`employee_id` / `business_date` / `trace_id`）由程序层注入，不进入 LLM `arguments`。`leave_proposal_tool` 只生成 Proposal / Clarification，不执行写操作。

- **safety_node**: Safety Guard Lite —— 启发式纵深防御过滤器（非授权/信任/权限边界）；NFKC+零宽字符+控制字符规范化，五族高置信规则（prompt_override / prompt_extraction / credential_extraction / tool_abuse / business_policy_bypass），明确攻击拦截、咨询放行，原始输入原样传给下游
- **router_node**（legacy Router-first）: 规则路由（eval 关键词 → eval，年假意图 → action，其他 → rag）
- **planner_node**（Planner-first）: 输出严格结构化的 PlannerDecision（Pydantic 严格白名单）；`MAX_PLANNER_STEPS=5`；最终决策可触发 `task_complete` / `refused` / `not_allowed` / `provider_error` / `invalid_decision` / `step_budget_exhausted`
- **tool_executor_node**（Planner-first）: 执行 Planner `action=tool` 决策；`MAX_TOOL_CALLS=3`；按结构 / 权限 / Tool 预算 / 成功签名去重顺序校验；任何执行前拦截不计数
- **rag_node**: Hybrid Retrieval (Faiss + BM25 + RRF) + LLM 生成
- **action_node**（legacy Router-first）: 年假申请受控业务动作确定性 Proposal 流程（不依赖 `JAVA_INTERNAL_TOKEN`）
- **eval_node**: 读取评估报告

### 检索模式

- **hybrid**（默认）: Faiss 语义检索 + BM25 + RRF 融合排序
- **vector**: Faiss + keyword 合并去重
- **hybrid_rerank**: Hybrid + Cross Encoder 精排（实验模式）

### 并发控制

Java 和 Python 都有并发限制：
- Java: `PythonAgentBulkhead`（Semaphore，默认 3 并发）
- Python: `ai_request_limiter`（默认 3 并发，500ms 队列超时）

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
| `REWRITE_MODE` | 查询重写 | none (可选 rule) |
| `RAG_GATE_MODE` | 检索门控 | off (可选 shadow) |
| `AGENT_LOOP_ENABLED` | 切换 LangGraph 两套状态图 | false（仓库部署默认；true 启用 Planner-first） |
| `JAVA_BASE_URL` | Python → Java 内部只读端点地址 | 空（只读企业 Tool 关闭；空时返回 `LEAVE_READ_DISABLED`） |
| `JAVA_INTERNAL_TOKEN` | Python → Java 内部只读端点鉴权 | 空（空时鉴权失败） |
| `JAVA_TIMEOUT_SECONDS` | Python → Java 内部只读超时秒数 | 5 |

### 环境变量（Java）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_TOKEN` | 管理员 Token | 空 = Demo 模式 |
| `BUSINESS_ACTIONS_ENABLED` | 启用业务动作 | false |
| `DEMO_IDENTITY_ENABLED` | 启用演示身份 | false |
| `SPRING_DATASOURCE_URL` | PostgreSQL URL | jdbc:postgresql://localhost:5432/enterprise_ai_copilot |

## 数据目录

- `data/hr/`, `data/bank/`, `data/it/`: 原始知识库文档（Markdown）
- `data/processed/`: 构建产物（chunks.json, faiss.index, faiss_metadata.json）
- `data/eval/`: 评估用例和报告（rag_eval_cases.json, reports/）

## 评估体系

- **检索评估**: source_hit_rate + keyword_hit_rate → final_pass_rate
- **生成评估**: keyword_groups 同义词组（组内 OR、组间 AND）
- **负样本**: 10 个 no-answer 用例验证拒答能力
- **Shadow Gate**: 实验性检索相关性门控（off/shadow）

## 注意事项

- Python 端 `DEEPSEEK_API_KEY` 未配置时，LLM 调用不可用，但检索评估仍可运行
- Faiss 索引和 metadata 在模块加载时初始化，文件不存在时仅警告不阻塞
- Embedding 模型首次 encode 时延迟加载
- Java 端 `admin.token` 为空时为 Demo 模式，所有用户可访问 eval 路由
- Business Action 默认关闭，需设置 `BUSINESS_ACTIONS_ENABLED=true` 启用
