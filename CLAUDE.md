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

```
START → safety_node → router_node → rag_node → END
                              ├→ eval_node → END
                              ├→ action_node → END
                              └→ refuse_node → END
```

- **safety_node**: Safety Guard Lite —— 启发式纵深防御过滤器（非授权/信任/权限边界）；NFKC+零宽字符+控制字符规范化，五族高置信规则（prompt_override / prompt_extraction / credential_extraction / tool_abuse / business_policy_bypass），明确攻击拦截、咨询放行，原始输入原样传给下游
- **router_node**: 规则路由（eval 关键词 → eval，年假意图 → action，其他 → rag）
- **rag_node**: Hybrid Retrieval (Faiss + BM25 + RRF) + LLM 生成
- **action_node**: 年假申请 Tool Calling 流程
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

年假申请的受控业务动作：
1. Python 生成 `action_proposal`（含 confirmationNonce）
2. Java 创建 `PendingAction`（PostgreSQL 持久化，TTL 过期）
3. 前端展示确认卡片，用户确认/取消
4. Java 执行确认（nonce 校验、幂等性、余额扣减）

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
