# AGENTS.md

## Repository Expectations

Enterprise AI Copilot 是一个企业知识库 AI 应用后端项目，采用 Java Spring Boot + Python FastAPI + React 三端架构。项目定位为 AI 应用后端工程实践，非模型训练项目。

## Project Layout

```
backend-java/      → Java Spring Boot 业务入口（port 8080）
agent-python/      → Python FastAPI AI 服务（port 8000）
frontend/          → React + Vite 前端演示（port 5173)
data/
  ├── hr/          → HR 知识库文档
  ├── it/          → IT 知识库文档
  ├── bank/        → 银行知识库文档
  ├── processed/   → chunks.json, faiss.index, faiss_metadata.json
  └── eval/        → eval cases, reports, baselines
docs/
  ├── architecture.md  → 架构说明
  ├── api.md           → 接口文档
  └── roadmap.md       → 功能规划
```

## Common Commands

```bash
# Python AI Service
cd agent-python && uv sync && uv run uvicorn app.main:app --reload --port 8000

# Java Backend
cd backend-java && ./mvnw spring-boot:run

# Frontend
cd frontend && npm install && npm run dev

# Build knowledge base
cd agent-python
uv run python scripts/build/build_chunks.py
uv run python scripts/build/build_embeddings.py
uv run python scripts/build/build_faiss_index.py

# Run evaluation
cd agent-python
uv run python scripts/eval/run_rag_eval.py
uv run python scripts/eval/run_rag_eval.py --with-baseline

# Update baseline (manual, only after 100% pass)
uv run python scripts/eval/update_eval_baseline.py

# TopK comparison
uv run python scripts/eval/compare_topk_eval.py --top-k-list 3,5,8
```

## Change Guidelines

- **改 Java API 时**：检查 Python 侧 DTO 是否匹配，同步更新 `docs/api.md`
- **改 Python RAG 时**：运行 `run_rag_eval.py` 确认无 regression
- **改 LangGraph Agent 时**：保持 `/agent/chat` 稳定主链路不被破坏
- **改 eval 脚本时**：确认 answerable 和 no-answer 两类 case 都正常
- **改 README / docs 时**：不要夸大未实现能力，experimental 模块不要写成 production-ready
- **改知识库文档时**：需要重新 build chunks / embeddings / faiss index
- **改 Prompt 时**：需要重新运行 eval 确认无 regression
- **不要提交**：`.env`、API key、token、`.venv/`、`target/`、`node_modules/`、current eval reports

## Safety / Security

- 不要暴露 API key 或 token 在代码或文档中
- 不要把用户输入直接当作工具参数执行
- RAG / Agent 输出默认不视为可信，需要验证
- 注意 LLM prompt injection 风险
- Safety Guard 是基于关键词的规则匹配，不是语义级安全

## Pull Request Checklist

- [ ] `git status` 工作区干净
- [ ] 无 `.env`、API key、token
- [ ] 无 `.venv/`、`target/`、`node_modules/`
- [ ] 无 current eval reports（除非是 baseline 更新）
- [ ] eval 通过或说明未运行原因
- [ ] docs 同步更新（api.md / architecture.md / roadmap.md / README.md）
- [ ] 不虚构未实现功能
