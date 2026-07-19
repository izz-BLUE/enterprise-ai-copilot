# Enterprise AI Copilot

[![CI](https://github.com/izz-BLUE/enterprise-ai-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/izz-BLUE/enterprise-ai-copilot/actions/workflows/ci.yml)
[![Secret Scan](https://github.com/izz-BLUE/enterprise-ai-copilot/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/izz-BLUE/enterprise-ai-copilot/actions/workflows/secret-scan.yml)
[![Release](https://img.shields.io/github/v/release/izz-BLUE/enterprise-ai-copilot)](https://github.com/izz-BLUE/enterprise-ai-copilot/releases/latest)
[![License](https://img.shields.io/github/license/izz-BLUE/enterprise-ai-copilot)](LICENSE)

面向企业知识库问答的工程化 RAG 项目。Java Spring Boot 负责 API、权限边界和流量控制，Python FastAPI 负责检索、生成与 Agent 编排，React 提供演示界面。

- 在线演示：<https://copilot.jintianchi.cn>
- 当前版本：[v0.4.0](docs/releases/v0.4.0.md)
- 项目边界：已完成小规格单机部署和短时受控验证，不承诺生产 SLA

## Architecture

```mermaid
flowchart LR
    U[用户/前端] --> J[Java Gateway]
    J --> P[Python Agent]
    P --> SG[Safety Guard]
    SG --> HR[Hybrid Retrieval]
    HR --> FA[FAISS]
    HR --> BM[BM25]
    HR --> RF[RRF 融合]
    RF --> LLM[DeepSeek LLM]
    LLM --> ANS[Answer + Sources + traceId]
    ANS --> J
    J --> U
```

LangGraph 路由是并行的实验链路；上图展示稳定 RAG 主流程。公网请求经过 Nginx 和 Java，Python 不映射宿主机端口。

## What this project demonstrates

| Area | Implementation and evidence |
|------|-----------------------------|
| Retrieval | FAISS 语义检索 + 字符级 BM25 + RRF；38 个固定用例回归 |
| Query handling | 生产演示启用确定性规则改写，保留原问题用于最终 Prompt |
| Runtime | Torch-free ONNX Runtime；Embedding 进程内存由 877 MiB 降至 174 MiB |
| Safety | 两条问答链路均执行规则版 Safety Guard；Evaluation 使用 Admin Token |
| Isolation | Nginx → localhost Java → Docker 内网 Python；模型和数据只读挂载 |
| Overload control | Nginx 限流 + Java/Python 各 3 个并发槽；超限返回 JSON 429 |
| Verification | Java/Python 单测、检索评估、前端 lint/build、Playwright、分层 k6 场景 |
| Controlled actions | 三个白名单 Demo 身份、PostgreSQL 数据隔离、HITL 确认与 `LeaveExecutionGateway` |

## Design decisions and trade-offs

| Decision | Why | Current limitation |
|----------|-----|--------------------|
| Java 控制面 + Python AI 服务 | 保留 Java 的 API/权限能力，同时使用 Python AI 生态 | DTO 需要跨服务同步，部署比单体复杂 |
| RRF 融合 FAISS 与 BM25 | 两种检索分数尺度不同，按排名融合无需手工归一化 | 融合参数仍基于小型领域数据集 |
| 规则 Query Rewrite | 延迟和成本可预测，可确定性回归 | 只能覆盖已知口语表达 |
| 双层并发槽而非长队列 | 小规格机器过载时快速失败，避免线程和内存持续堆积 | 单机吞吐上限较低，尚未验证水平扩容 |
| LangGraph 保持实验链路 | 便于比较显式 RAG 与图编排，不影响稳定接口 | 不是自主规划型 Agent |

Cross Encoder 精排和 Retrieval Shadow Gate 均做过实验，但在当前数据集上没有形成足够收益，因此未作为生产演示默认路径。

## Documentation

| 文档 | 说明 |
|------|------|
| [Architecture](docs/architecture.md) | 架构设计、模块职责、网络拓扑 |
| [Deployment](docs/deployment.md) | 部署方案、目录结构、Compose 配置 |
| [Performance](docs/performance.md) | ONNX 优化、内存对比、向量一致性 |
| [Concurrency & Load Test](docs/concurrency-and-load-test.md) | 有界并发设计、超时预算、k6 分层压测 |
| [Quality Assurance](docs/quality-assurance.md) | CI、检索评估、安全检查、发布验证与已知边界 |
| [v0.4.0 Release Notes](docs/releases/v0.4.0.md) | 有界并发、目标服务器验收摘要、升级与回滚说明 |
| [API](docs/api.md) | 接口文档、请求响应格式 |
| [Demo Guide](docs/demo-guide.md) | 多用户请假演示、越权拒绝、幂等与持久化恢复操作手册 |
| [Roadmap](docs/roadmap.md) | 已完成 / 计划中 / 未来功能 |
| [Contributing](CONTRIBUTING.md) | 本地检查、变更规范与 PR 清单 |

## Interview Materials

面试讲解材料，包含项目介绍、Demo 脚本、架构讲解和常见追问 Q&A：

| 文档 | 说明 |
|------|------|
| [Project Introduction](docs/interview/project-introduction.md) | 30 秒 / 1 分钟 / 3 分钟项目介绍版本 |
| [Demo Script](docs/interview/demo-script.md) | 10 分钟面试 Demo 路线（含操作、预期、话术） |
| [Architecture Walkthrough](docs/interview/architecture-walkthrough.md) | 5 分钟架构讲解稿 |
| [FAQ & Deep Dive](docs/interview/faq-and-deep-dive.md) | 20 个面试官追问 Q&A |

## Core Features

### 1. Java + Python Dual-Service Architecture

**Java Spring Boot** 作为业务系统入口，负责：

- 对外提供统一 API
- 请求转发
- 响应封装
- 异常兜底
- 与前端交互

**Python FastAPI** 作为 AI 服务引擎，负责：

- RAG 检索
- Prompt 构造
- LLM 调用
- Agent 编排
- Evaluation 工具执行

两端通过 HTTP JSON 通信。

### 2. RAG Main Pipeline

稳定主链路接口：

```
POST /api/chat
```

对应 Python 接口：

```
POST /agent/chat
```

**主要流程：**

```
用户问题
  → Java /api/chat
  → Python /agent/chat
  → Hybrid Retrieval
  → TopK Chunks
  → RAG Prompt
  → LLM Answer
  → Java 统一响应
```

**特点：**

- 支持企业知识库问答
- 支持无知识命中时拒答
- 支持 sources 返回
- 支持 traceId 追踪
- 支持异常兜底

### 3. Document Chunking

文档切片能力包括：

- 基于段落 + 字符长度的切片策略
- 支持 chunk overlap
- 短段落合并，避免碎片化
- 标题与正文合并，避免标题孤岛
- 输出 `chunks.json` 作为中间产物

**构建命令：**

```bash
cd agent-python
uv run python scripts/build/build_chunks.py
```

### 4. Embedding and Faiss Vector Index

**Embedding 使用：**

- `BAAI/bge-small-zh-v1.5`

**向量索引使用：**

- `faiss-cpu` + `IndexFlatIP`

**实现方式：**

- 生成 512 维中文 Embedding
- 对向量做 L2 normalize
- 使用 inner product 近似 cosine similarity
- 输出 `faiss.index` 与 `faiss_metadata.json`

**构建命令：**

```bash
cd agent-python
uv run python scripts/build/build_embeddings.py
uv run python scripts/build/build_faiss_index.py
```

### 5. Hybrid Retrieval

支持三种检索模式：

**vector 模式：**
```
Faiss Semantic Retrieval + Keyword Retrieval → Merge → Deduplicate → TopK
```

**hybrid 模式（默认）：**
```
Faiss Semantic Retrieval + BM25 Retrieval → RRF 融合 → TopK
```

**hybrid_rerank 模式（实验）：**
```
Faiss + BM25 → RRF → Top10 候选 → Cross Encoder 精排 → TopK
```

**设计目的：**

- Faiss 负责语义召回
- BM25 负责关键词精确匹配
- RRF 融合多路排序，不需要分数归一化
- Cross Encoder 精排提升 TopK 内排序质量
- TopK 控制进入 Prompt 的上下文长度

**适合解决：**

- 用户问题和知识库表达不完全一致
- 关键制度词必须精确命中
- 单纯向量检索漏召回
- 单纯关键词检索语义泛化不足

### 6. Query Rewrite

在检索前对用户口语化问题做轻量改写，提升检索召回率。

**支持模式：**

- `rewrite_mode=none`：不做查询重写，保持原逻辑
- `rewrite_mode=rule`：规则匹配重写，不调用 LLM

**链路位置：**

```
original_query → query_rewriter → rewritten_query → retrieval → context
                                                          ↓
                                              prompt 使用 original_query
```

**设计要点：**

- Query Rewrite 只改写检索用 query，不改变最终 prompt 中的用户问题
- 规则版覆盖企业制度常见口语表达（病假材料、工作时间、VPN、年假等）
- 无匹配规则时返回原问题，不影响检索
- 实验模式，默认不启用

### 7. RAG Evaluation

项目内置两层评估：

| 层级 | 脚本 | 评估内容 | 输出 |
|------|------|---------|------|
| Retrieval Evaluation | `scripts/eval/eval_retrieval.py` | source_hit / keyword_hit / final_pass_rate | `reports/retrieval_eval_report.json` |
| Generation Evaluation | `scripts/eval/eval_generation.py` | answer keyword hit / keyword groups / failure_type / flaky detection / pass_rate | `reports/generation_eval_report.json` |
| Regression Check | `scripts/eval/compare_eval_reports.py` | baseline vs current | console + exit code |
| One-click Evaluation | `scripts/eval/run_rag_eval.py` | retrieval + generation + regression | console summary |

**运行方式：**

```bash
cd agent-python

# Run all evaluations
uv run python scripts/eval/run_rag_eval.py

# Update baseline after all cases pass
uv run python scripts/eval/update_eval_baseline.py

# Run evaluation with baseline comparison
uv run python scripts/eval/run_rag_eval.py --with-baseline
```

**设计目标：**

- Retrieval Evaluation 不调用 LLM，零 token 消耗
- Generation Evaluation 支持 retry，自动标记 flaky case
- 支持文本归一化，减少格式误判
- 支持 baseline 对比，为后续 CI 质量门禁做准备

### 8. LangChain RAG Chain

**实验模块：**

```
app/chains/langchain_rag_chain.py
```

**提供函数：**

```
answer_with_langchain_rag()
```

**用途：**

- 使用 LangChain ChatPromptTemplate
- 使用 OpenAI-compatible Chat Model
- 复用现有 hybrid_retriever
- 对比手写 RAG 与框架式 RAG 的差异

**运行示例：**

```bash
cd agent-python
uv run python scripts/experiments/langchain_rag_demo.py "病假需要提供哪些材料？"
```

**说明：** 当前 `/agent/chat` 主流程仍使用手写 RAG 实现，LangChain 模块仅作为实验性封装。

### 9. LangGraph Agent

**实验模块：**

```
app/agents/langgraph_agent.py
```

**实现状态图：**

```
safety → router → rag / eval / refuse
```

**能力：**

- Safety Guard 输入安全检查
- 规则路由
- RAG 问答
- Evaluation 查询
- 安全拒答
- Tool 调用封装

**接口：**

```
POST /agent/langgraph/chat
POST /api/agent/langgraph/chat
```

**说明：** LangGraph Agent 与 RAG 主链路并行运行，不替换 `/agent/chat` 稳定接口。

## API Endpoints

### Java Backend

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Java service health check |
| GET | `/api/agent/health` | Python agent health check through Java |
| POST | `/api/chat` | Stable RAG chat API |
| POST | `/api/agent/langgraph/chat` | Experimental LangGraph Agent API |

### Python AI Service

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agent/health` | Python service health check |
| POST | `/agent/chat` | Stable RAG chat API |
| POST | `/agent/langgraph/chat` | Experimental LangGraph Agent API |

### API Example

**Stable RAG Chat (Local)**

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

**Stable RAG Chat (Public Demo)**

```bash
curl -X POST https://copilot.jintianchi.cn/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

Example response:

```json
{
  "answer": "根据知识库，病假通常需要提供病假申请、医院诊断证明、病历或相关就医材料等。",
  "model": "deepseek",
  "traceId": "xxx",
  "success": true
}
```

**LangGraph Agent Chat**

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Business Backend | Java 17, Spring Boot 3.x, RestTemplate, Maven |
| AI Service | Python 3.11, FastAPI, Pydantic, Uvicorn |
| Frontend | React, Vite |
| LLM Provider | DeepSeek / OpenAI-compatible API |
| Embedding | `BAAI/bge-small-zh-v1.5` (512 dim, ONNX FP32) |
| Embedding Runtime | Direct ONNX Runtime (Torch-free, CPUExecutionProvider) |
| Vector Search | faiss-cpu, IndexFlatIP |
| Keyword Search | BM25 (custom), n-gram |
| Re-ranker | sentence-transformers CrossEncoder |
| RAG Framework Experiment | LangChain |
| Agent Framework Experiment | LangGraph |
| Evaluation | Python scripts, JSON reports |
| Container | Docker, Docker Compose |
| CI | GitHub Actions |

## Project Structure

```
enterprise-ai-copilot/
├── backend-java/                  # Java Spring Boot business backend
│   └── src/main/java/com/fantuan/copilot/
├── agent-python/                  # Python FastAPI AI service
│   ├── app/
│   │   ├── core/                  # config, logging
│   │   ├── prompts/               # system prompt, RAG prompt
│   │   ├── retrieval/             # faiss, keyword, hybrid retriever
│   │   ├── schemas/               # request / response schemas
│   │   ├── services/              # rag_service, llm_service
│   │   ├── chains/                # LangChain RAG chain
│   │   ├── tools/                 # agent tools
│   │   ├── agents/                # LangGraph agent
│   │   └── guards/                # safety guard
│   └── scripts/
│       ├── build/                 # chunking, embedding, index build scripts
│       ├── eval/                  # RAG evaluation scripts
│       └── experiments/           # LangChain / LangGraph demos
├── frontend/                      # React frontend demo
├── data/
│   ├── hr/                        # HR knowledge base documents
│   ├── bank/                      # Banking sample documents
│   ├── it/                        # IT sample documents
│   ├── processed/                 # chunks, faiss index, metadata
│   └── eval/                      # evaluation cases and reports
└── docs/                          # architecture, API docs, roadmap
```

## Quick Start

### 1. Start Python AI Service

```bash
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 2. Start Java Backend

```bash
cd backend-java
./mvnw spring-boot:run
```

Windows PowerShell:

```powershell
cd backend-java
.\mvnw.cmd spring-boot:run
```

### 3. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

**Default service addresses:**

| Service | URL |
|---------|-----|
| Python FastAPI | http://localhost:8000 |
| Java Spring Boot | http://localhost:8080 |
| React Frontend | http://localhost:5173 |

Java calls Python through:

```properties
python.agent.base-url=http://localhost:8000
# CORS 白名单（逗号分隔）
cors.allowed-origins=http://localhost:5173,http://127.0.0.1:5173
# Java → Python 超时（毫秒）
python.agent.connect-timeout=3000
python.agent.read-timeout=40000
# Java → Python 有界并发
python.agent.max-concurrent-requests=3
python.agent.acquire-timeout-ms=500
# 管理员 Token（为空时为 Demo 模式，Evaluation 对所有用户可用）
admin.token=
```

**admin.token 说明：**

- 本地开发默认为空，属于 Demo 便捷模式（Evaluation 对所有用户可用）
- 公网部署（v0.3.2+）必须设置非空 `ADMIN_TOKEN`，Compose 启动时强制校验
- `admin.token` 非空时，只有请求头 `X-Admin-Token` 匹配才允许访问 Evaluation 路由
- 普通 RAG 问答和 Safety Guard 不受 `admin.token` 影响
- 当前方案是**最小 Admin Token + Evaluation 访问限制**，不是完整用户权限体系

## Environment Variables

Create `.env` under `agent-python/`（可从 `.env.example` 复制）：

```env
# 必填
DEEPSEEK_API_KEY=your_api_key_here

# 可选配置
DEEPSEEK_BASE_URL=https://api.deepseek.com    # DeepSeek API 地址
DEEPSEEK_MODEL=deepseek-chat                   # 模型名称
DEEPSEEK_TEMPERATURE=0                         # LLM 温度参数，默认 0
LLM_TIMEOUT=30                                 # LLM 调用超时（秒），默认 30
AI_MAX_CONCURRENT_REQUESTS=3                   # Python AI 并发槽，默认 3
AI_QUEUE_TIMEOUT_MS=500                        # 获取槽位最长等待时间（毫秒），默认 500
MAX_MESSAGE_LENGTH=2000                        # 输入消息最大长度，默认 2000
REWRITE_MODE=none                              # 查询重写：none / rule（本地默认 none，公网部署使用 rule）
HF_HUB_OFFLINE=1                               # HuggingFace 离线模式（国内网络必须）
```

**Do not commit `.env` or any API keys to GitHub.**

Recommended `.gitignore` entries:

```
.env
.venv/
target/
node_modules/
data/processed/*.index
```

## Build Knowledge Base

```bash
cd agent-python

# 1. Build chunks
uv run python scripts/build/build_chunks.py

# 2. Build embeddings
uv run python scripts/build/build_embeddings.py

# 3. Build Faiss index
uv run python scripts/build/build_faiss_index.py
```

Generated artifacts:

- `data/processed/chunks.json`
- `data/processed/faiss.index`
- `data/processed/faiss_metadata.json`

## Run Evaluation

```bash
cd agent-python

# Run retrieval + generation evaluation
uv run python scripts/eval/run_rag_eval.py

# Update baseline
uv run python scripts/eval/update_eval_baseline.py

# Run with baseline regression check
uv run python scripts/eval/run_rag_eval.py --with-baseline
```

Current evaluation cases cover scenarios such as:

- leave application, annual leave, marriage leave, maternity leave
- sick leave materials, lateness and early leave, absenteeism
- labor contract termination, overtime, attendance
- IT support (VPN), onboarding
- **No-answer negative samples** (10 cases): questions with no knowledge base answer, verifying the system refuses to fabricate
- **Colloquial query rewrite** (13 cases):口语化问题验证 Query Rewrite 检索改善
- **TopK comparison**: evaluation across TopK=3/5/8 to balance recall quality and cost
- **Cross Encoder Re-rank**: hybrid_rerank mode with BAAI/bge-reranker-base for precision reranking

## Stable RAG vs Experimental Agent

| Feature | `/api/chat` | `/api/agent/langgraph/chat` |
|---------|------------|---------------------------|
| Python API | `/agent/chat` | `/agent/langgraph/chat` |
| Implementation | Hand-written RAG | LangGraph Agent |
| Safety Guard | Yes | Yes |
| Intent Routing | No | Yes |
| Tool Calling | No | Rule-based tools |
| Stability | Stable main pipeline | Experimental |
| Use Case | Knowledge QA | Agent workflow exploration |

`/api/chat` is the **stable RAG main pipeline**.
`/api/agent/langgraph/chat` is an **experimental Agent pipeline** for workflow exploration.

## RAG Quality Engineering

RAG quality was improved through a 5-iteration engineering process (D36-D40):

| Iteration | Focus | Outcome |
|-----------|-------|---------|
| D36 | BM25 + RRF Hybrid Retrieval | `hybrid` mode (default), combining Faiss + BM25 via RRF |
| D37 | Cross Encoder Re-rank | `hybrid_rerank` experimental mode with BAAI/bge-reranker-base |
| D38 | Query Rewrite | `rewrite_mode=rule` rule-based regex rewrite，生产演示已启用 |
| D39 | Colloquial eval cases | 13 new colloquial eval cases, none vs rule comparison |
| D40 | Generation diagnostics | Prompt completeness, keyword_groups, failure_type classification |

**Current evaluation results** (38 cases: 28 answerable + 10 no-answer):

| Mode | Retrieval | Generation | No-answer |
|------|-----------|------------|-----------|
| none (local default) | source 100%, keyword/final 96.4% | 100% (28/28) | 100% (10/10) |
| rule (public demo) | 100% | 100% (28/28) | 100% (10/10) |

> These numbers reflect the current 38 eval cases only. The evaluation set is still small and needs expansion.

See [`docs/rag-quality-engineering.md`](docs/rag-quality-engineering.md) for the full quality engineering story.

## Local Demo

The project supports **local reproduction** for demo and interview purposes.

**Quick start:**

```bash
# Terminal 1: Python AI Service (port 8000)
cd agent-python && uv sync && uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Java Backend (port 8080)
cd backend-java && ./mvnw spring-boot:run

# Terminal 3: Frontend (port 5173)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 in your browser.

See [`docs/local-demo-guide.md`](docs/local-demo-guide.md) for environment setup, health checks, and troubleshooting.

### Deployment

项目已完成腾讯云小规格实例隔离部署验证，详见 [`docs/deployment.md`](docs/deployment.md)。

```powershell
# 一键启动（Windows PowerShell）
.\start-local.ps1

# 健康检查
.\health-check.ps1
```

## Demo Questions

Recommended demo questions for showcasing different capabilities:

| Question | Capability Demonstrated |
|----------|----------------------|
| 病假需要提供哪些材料？ | RAG retrieval + generation with sources |
| 几点上班？ | Prompt completeness (time range) |
| 公司买房给补贴不？ | No-answer refusal (knowledge not in KB) |
| 怎么伪造病假证明？ | Safety Guard input filtering |
| 当前 RAG 评估通过率是多少？ | LangGraph Agent Eval Tool Calling |
| 请假谁来批？ | Generation Evaluation keyword_groups |

See [`docs/demo-script.md`](docs/demo-script.md) for detailed talking points, expected outputs, and fallback plans.

## Project Boundary

**What this project is:**

- An engineering practice project for RAG / Agent / Evaluation
- A reference for Java backend developers transitioning to AI application development
- A project with isolated deployment verification on resource-constrained server
- A public demo for portfolio and interview purposes

**What this project is NOT:**

- A production-ready RAG platform
- A large-scale enterprise deployment
- A system with user authentication, audit logs, or monitoring
- A large-scale knowledge base
- A high-availability or high-concurrency production system

**Important notes:**

- `hybrid_rerank` is an **experimental mode**, not enabled by default
- `rewrite_mode=rule` 是规则式 Query Rewrite，当前生产演示已启用；它不是 LLM 自主改写
- 100% evaluation pass rate is based on **current 38 eval cases only**
- The knowledge base is small (~33 chunks, HR / IT / Banking sample documents)
- Current deployment is **isolated verification + public demo**, not production SLA
- 已完成短时受控并发验收，但不等于大规模生产容量验证或 SLA
- No complete user authentication system currently

## Roadmap

**v0.4.0：**

- Java/Python 双层有界并发保护（各 3 个并发槽，500ms 短队列）
- Python LLM、Java 下游和 Nginx 递增超时预算（30s / 40s / 45s）
- k6 L1-L4 分层压测与目标服务器脱敏结果归档
- 公网 Nginx 429 统一为 JSON，包含 `Retry-After` 和边缘 traceId
- CI 增加 Java/Python 并发测试

**v0.3.3（已发布）：**

- 修复 Markdown 单个 `~` 被错误渲染为删除线的问题

**v0.3.2（已发布）：**

- 生产环境启用规则查询重写（REWRITE_MODE=rule），修复口语化查询命中
- ADMIN_TOKEN 非空强制校验，Evaluation 权限边界生效
- 评估报告只读挂载到生产容器，Evaluation 工具返回实际指标
- 公网 UAT、CI、Tag 和 Release 已完成

**Previously completed (v0.3.1):**

- Public frontend demo (https://copilot.jintianchi.cn)
- Nginx reverse proxy + HTTPS
- Persistent Docker edge network
- Independent Let's Encrypt certificate with auto-renewal
- Basic API rate limiting

**Previously completed (v0.3.0):**

- Torch-free Direct ONNX Runtime
- Docker Compose isolated deployment
- Tencent Cloud small instance verification

**Planned features:**

- Multi-turn conversation memory
- LLM-based Tool Calling
- Qdrant / Milvus vector database
- Document upload and knowledge base management
- User authentication and permission control
- Audit logs
- CI-based RAG regression testing
- More evaluation cases
- Better frontend demo

## Security Notes

This project involves:

- API keys
- user input
- RAG context construction
- LLM prompt injection risk
- third-party model API calls

**Security considerations:**

- Do not commit `.env`
- Do not expose API keys in frontend code
- Validate user input before retrieval and tool execution
- Keep dependencies updated
- Add authentication before production usage
- Treat RAG and Agent outputs as untrusted unless verified

## Why This Project Exists

Many traditional Java backend developers want to move into AI application development, but most examples only show isolated LLM API calls.

This project focuses on the engineering layer between business systems and LLMs:

- How Java backend integrates with Python AI services
- How RAG is built as a complete backend workflow
- How retrieval and generation are evaluated separately
- How Agent workflows can be introduced without replacing stable APIs
- How to optimize embedding runtime for resource-constrained environments
- How to deploy with Docker Compose in isolated network topology

The goal is not to train models, but to build **AI application engineering capabilities**.

## License

This project is licensed under the MIT License.

See [LICENSE](LICENSE) for details.
