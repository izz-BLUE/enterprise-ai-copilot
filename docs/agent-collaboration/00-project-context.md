# 00 - 项目上下文（Project Context）

> **所有开发会话启动前必须先读此文档。**

## 项目定位

Enterprise AI Copilot 是一个**本地可复现的 RAG 应用后端 Demo**，定位为：

- 企业 AI 应用后端工程实践
- Java 后端转 AI 应用开发的参考项目
- RAG / Agent / Evaluation 工程链路实验项目
- 生产化改造候选项目

**不是什么：**

- ❌ 已上线的生产系统
- ❌ 支持大规模并发的平台
- ❌ 有完整权限体系的应用
- ❌ 已部署到公网的服务

## 当前状态

| 层 | 状态 | 端口 | 说明 |
|---|---|---|---|
| React Frontend | 可运行，基础演示 | 5173 | 无认证、无状态管理 |
| Java Spring Boot | 稳定 | 8080 | 统一入口、代理 Python、traceId |
| Python FastAPI | 稳定 | 8000 | RAG + Agent + Evaluation |
| RAG Evaluation | 完整闭环 | — | 38 cases，100% 通过率（基于当前 case 集） |

## 两条主链路

| 链路 | 接口 | 状态 | 说明 |
|---|---|---|---|
| RAG 主链路 | `POST /api/chat` → `/agent/chat` | **稳定** | 手写 RAG，不依赖 LangChain |
| Agent 链路 | `POST /api/agent/langgraph/chat` → `/agent/langgraph/chat` | **实验** | LangGraph，Safety Guard + 意图路由 |

## 实验功能（不得默认启用）

| 功能 | 配置 | 说明 |
|---|---|---|
| Cross Encoder Re-rank | `retrieval_mode=hybrid_rerank`（函数参数） | 当前评估集提升不显著 |
| Rule-based Query Rewrite | `REWRITE_MODE=rule` | 规则有限，仅覆盖部分口语化表达 |
| LangChain RAG Chain | 独立实验模块 | 不作为主链路 |

## 技术栈

| 层 | 技术 |
|---|---|
| Business Backend | Java 17, Spring Boot 3.x, RestTemplate, Maven |
| AI Service | Python 3.11, FastAPI, Pydantic, Uvicorn |
| Frontend | React 19, Vite |
| LLM | DeepSeek / OpenAI-compatible |
| Embedding | BAAI/bge-small-zh-v1.5 (512维) |
| Vector Search | faiss-cpu, IndexFlatIP |
| Keyword Search | BM25 (字符级 n-gram，无外部依赖) |
| Re-ranker | sentence-transformers CrossEncoder（实验） |
| Evaluation | Python scripts, JSON reports |

## 已知风险

| 风险 | 说明 |
|---|---|
| HuggingFace 离线 | 国内网络必须设置 `HF_HUB_OFFLINE=1` |
| DTO 契约 | Java ↔ Python 手动对齐，无共享 schema |
| 评估集规模 | 38 个 case，覆盖场景有限 |
| 知识库规模 | 33 个文档片段 |
| API Key | `.env` 本地存储，不能提交 |

## 启动顺序

```bash
# 1. Python AI Service
cd agent-python && uv sync && uv run uvicorn app.main:app --reload --port 8000

# 2. Java Backend
cd backend-java && ./mvnw spring-boot:run

# 3. Frontend
cd frontend && npm install && npm run dev
```

## 必读文档

| 文档 | 路径 | 何时读 |
|---|---|---|
| 项目全貌 | `README.md` | 启动前 |
| 架构设计 | `docs/architecture.md` | 涉及架构变更时 |
| API 文档 | `docs/api.md` | 涉及接口变更时 |
| 本地演示 | `docs/local-demo-guide.md` | 需要启动服务时 |
| 演示脚本 | `docs/demo-script.md` | 面试演示前 |
| RAG 质量 | `docs/rag-quality-engineering.md` | 涉及 RAG / Eval 时 |
| 旧会话交接 | `docs/agent-collaboration/handoff/legacy-dev-session-handoff.md` | 了解历史状态时 |
| 不可修改清单 | `docs/agent-collaboration/06-do-not-touch.md` | 任何开发前 |
| Task Board | `docs/agent-collaboration/04-task-board.md` | 领取任务前 |
