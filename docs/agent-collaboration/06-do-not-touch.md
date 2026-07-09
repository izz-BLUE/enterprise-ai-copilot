# 06 - 不可修改清单（Do Not Touch）

> **以下文件和模块在没有明确授权的情况下不得修改。修改前必须在 Task Board 登记并获得架构 Owner 确认。**

## 绝对禁止

| 文件/目录 | 原因 |
|---|---|
| `.env` | 包含 API Key，不能提交或修改 |
| `data/eval/reports/*` | eval 产物，自动生成，不应手动修改 |
| `data/processed/faiss.index` | 向量索引，构建脚本生成 |
| `data/processed/faiss_metadata.json` | 索引元数据，构建脚本生成 |
| `data/processed/chunks.json` | 文档切片，构建脚本生成 |
| `__pycache__/` | Python 缓存 |
| `node_modules/` | 前端依赖 |
| `target/` | Java 构建产物 |
| `.venv/` | Python 虚拟环境 |

## 需要架构 Owner 授权

| 文件/模块 | 原因 |
|---|---|
| `agent-python/app/prompts/system_prompt.py` | Prompt 影响所有 eval 结果 |
| `agent-python/app/retrieval/hybrid_retriever.py` | 检索核心逻辑 |
| `data/eval/rag_eval_cases.json` | 评估用例集，改动影响回归 |
| `data/eval/reports/*_eval_baseline.json` | 评估基线，回归检测基准 |
| `README.md` | 项目全貌，多人改会冲突 |
| `docs/architecture.md` | 架构文档 |
| `docs/api.md` | 接口契约（需同步 Java + Python） |

## 需要 AI/RAG 工程师授权

| 文件/模块 | 原因 |
|---|---|
| `agent-python/app/retrieval/faiss_retriever.py` | 向量检索 |
| `agent-python/app/retrieval/bm25_retriever.py` | BM25 检索 |
| `agent-python/app/retrieval/cross_encoder_reranker.py` | Re-rank（实验） |
| `agent-python/app/retrieval/query_rewriter.py` | Query Rewrite（实验） |
| `agent-python/app/services/rag_service.py` | RAG 主服务 |
| `agent-python/app/services/llm_service.py` | LLM 调用 |
| `agent-python/app/agents/langgraph_agent.py` | LangGraph Agent |
| `agent-python/app/guards/safety_guard.py` | Safety Guard |

## 需要全栈开发授权

| 文件/模块 | 原因 |
|---|---|
| `backend-java/src/main/java/com/fantuan/backend/controller/*` | Java Controller |
| `backend-java/src/main/java/com/fantuan/backend/filter/*` | Java Filter |
| `backend-java/src/main/java/com/fantuan/backend/config/*` | Java Config |
| `frontend/src/App.jsx` | 前端主组件 |
| `frontend/vite.config.js` | Vite 配置（含 proxy） |

## 禁止操作

| 操作 | 原因 |
|---|---|
| 默认启用 `hybrid_rerank` | 实验模式，当前评估集提升不显著 |
| 默认启用 `rewrite_mode=rule` | 实验模式，规则有限 |
| 修改知识库文档内容 | 影响 eval 结果 |
| 删除 eval baseline | 回归检测基准 |
| 在 main 分支直接开发 | 必须用 feature 分支 |
| 前端直接调用 Python API | 架构约束：前端 → Java → Python |
| 提交 `.env` 或 API Key | 安全约束 |
| 多会话同时修改同一模块 | 并发冲突 |

## 修改流程

1. 在 `04-task-board.md` 登记任务
2. 获得对应 Owner 确认
3. 创建 feature 分支
4. 修改后运行验证（eval / compile / dev）
5. 更新相关文档
6. 提交 PR / squash merge
