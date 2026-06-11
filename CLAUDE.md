# CLAUDE.md

## Project Overview

Enterprise AI Copilot 是一个企业知识库 AI 应用后端项目，采用 Java Spring Boot + Python FastAPI + React 三端架构，支持 RAG 检索增强生成问答、LangGraph Agent 实验链路和两层评估体系。

## Current Architecture

```
frontend/          → React + Vite (port 5173)
backend-java/      → Java Spring Boot (port 8080)，统一入口，代理 Python
agent-python/      → Python FastAPI (port 8000)，RAG + Agent + Evaluation
data/              → 知识库文档、评估测试集、FAISS 索引
docs/              → 架构、API、Roadmap 文档
```

**两条主链路：**

- `/api/chat` → `/agent/chat`：稳定 RAG 主链路（手写全链路，不依赖 LangChain）
- `/api/agent/langgraph/chat` → `/agent/langgraph/chat`：实验性 LangGraph Agent 链路（Safety Guard + 意图路由 + Tool Calling）

两条链路并行运行，Agent 链路不替换 RAG 稳定接口。

## Important Documents

- `README.md` — 项目全貌、Quick Start、功能列表
- `docs/architecture.md` — 架构图、模块说明、Hybrid Retrieval 设计
- `docs/api.md` — 接口文档、请求响应格式、curl 示例
- `docs/roadmap.md` — 已完成 / 计划中 / 未来功能

## Development Commands

```bash
# Python AI Service (port 8000)
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000

# Java Backend (port 8080)
cd backend-java
./mvnw spring-boot:run

# Frontend (port 5173)
cd frontend
npm install
npm run dev

# Build knowledge base
cd agent-python
uv run python scripts/build/build_chunks.py
uv run python scripts/build/build_embeddings.py
uv run python scripts/build/build_faiss_index.py

# Run evaluation
cd agent-python
uv run python scripts/eval/run_rag_eval.py
uv run python scripts/eval/run_rag_eval.py --with-baseline

# Update baseline (only after all cases pass and manual review)
uv run python scripts/eval/update_eval_baseline.py

# TopK comparison
uv run python scripts/eval/compare_topk_eval.py --top-k-list 3,5,8
```

## Coding Rules

- 不要提交 `.env`、API key、token、密码
- 不要提交 `.venv/`、`target/`、`node_modules/`
- 不要提交 current eval reports（`data/eval/reports/`），除非明确是 baseline
- 修改接口时同步更新 `docs/api.md`
- 修改架构时同步更新 `docs/architecture.md`
- 修改 RAG / Agent / Eval 行为时同步更新 `README.md` 或 `docs/roadmap.md`
- 不要虚构未实现功能
- 不要把 experimental 模块写成 production-ready
- `/agent/chat` 是稳定主链路，修改时必须确认不影响已有行为
- Python 评估脚本不引入新依赖

## Testing / Verification

修改后优先运行：

```bash
# Python 改动 → 运行 eval
cd agent-python && uv run python scripts/eval/run_rag_eval.py

# Java 改动 → 编译检查
cd backend-java && ./mvnw compile

# Frontend 改动 → 启动检查
cd frontend && npm run dev

# 确认工作区干净
git status
```
