# Legacy Dev Session Handoff

## 1. 基本信息

| 项目 | 值 |
|------|-----|
| 项目名称 | Enterprise AI Copilot |
| 当前分支 | `main` |
| 归档时间 | 2026-07-01 |
| 当前会话身份 | Legacy Dev Session（原开发会话） |
| 归档目的 | 为后续多 Claude Code 会话协作做交接 |

## 2. Git 当前状态

### 分支

当前在 `main` 分支，up to date with `origin/main`。

### 最近 10 个 commit

```
4452b1b docs: add local demo guide and RAG quality docs
18d86e4 feat: improve RAG answer completeness and generation evaluation diagnostics
63709b0 test: add colloquial query rewrite eval cases and expand rewrite rules
c1f828d feat: add rule-based query rewrite for RAG retrieval
e9a1993 feat: add experimental Cross Encoder rerank mode (hybrid_rerank)
39369e2 feat: add BM25 and RRF hybrid retrieval (#9)
cd05421 docs: add security policy (#7)
3759839 docs: add contributing guide (#6)
562ad4f ci: fix workflow checks (#8)
7b39ff5 ci: normalize workflow yaml formatting
```

### Uncommitted changes

| 状态 | 文件 | 说明 |
|------|------|------|
| `M` | `agent-python/app/__pycache__/main.cpython-311.pyc` | Python 缓存，不应提交 |
| `M` | `data/eval/reports/generation_eval_report.json` | eval 产物，不应提交 |
| `M` | `data/eval/reports/retrieval_eval_report.json` | eval 产物，不应提交 |
| `M` | `docs/local-demo-guide.md` | D41 后续小改动（HF_HUB_OFFLINE 说明） |
| `??` | `data/eval/reports/query_rewrite_comparison.json` | eval 产物，不应提交 |
| `??` | `data/eval/reports/query_rewrite_comparison.md` | eval 产物，不应提交 |

### 建议

- eval reports（`data/eval/reports/`）和 `__pycache__` 不应提交，已在 `.gitignore` 规则范围内
- `docs/local-demo-guide.md` 的 HF_HUB_OFFLINE 改动可以提交（小文档修正）
- 当前状态：**可以保持不动**，不影响后续协作

### 本地 feature 分支

以下分支已 squash merge 到 main，本地分支可清理：

- `feat/d40-generation-eval-optimize`
- `feat/d41-local-demo-guide`
- `feat/d38-query-rewrite`
- `feat/d39-colloquial-eval-cases`
- `feat/bm25-rrf-hybrid-retrieval`
- `feat/d37-cross-encoder-rerank`

## 3. 当前项目总体状态

### Java Spring Boot 服务

**状态：稳定，可运行。**

- Spring Boot 3.5.14，Java 17
- 端口 8080
- 职责：统一入口、traceId 管理、代理 Python 接口、CORS、异常兜底
- 关键类：`ChatController`、`LangGraphAgentController`、`TraceIdFilter`、`WebConfig`
- 不直接调用 LLM，不直接访问知识库

### Python FastAI / RAG / Agent 服务

**状态：稳定，可运行。**

- FastAPI，端口 8000
- 职责：RAG 检索、Prompt 构造、LLM 调用、LangGraph Agent 编排
- 关键模块：`rag_service.py`、`hybrid_retriever.py`、`system_prompt.py`、`langgraph_agent.py`
- 依赖：sentence-transformers、faiss、openai、langgraph
- 模型：BAAI/bge-small-zh-v1.5（Embedding）、BAAI/bge-reranker-base（Re-rank，实验模式）

### React 前端

**状态：可运行，基础演示级别。**

- React 19 + Vite，端口 5173
- 通过 Vite proxy 转发 `/api` 到 Java 8080
- 页面能力：聊天输入、RAG/Agent 模式切换、traceId 展示、sources 展示
- 无用户认证、无状态管理、无路由

### RAG Evaluation

**状态：完整闭环，可复跑。**

- 38 个 eval cases（28 answerable + 10 no-answer）
- Retrieval eval：100%（source_hit + keyword_hit）
- Generation eval：100%（answerable + no-answer）
- 支持 flaky 检测、baseline 回归、TopK 对比、Query Rewrite 对比
- failure_type 分类：passed / keyword_too_strict / generation_incomplete / llm_flaky / no_answer_leakage

### 本地 Demo

**状态：可演示。**

- 三端本地启动即可演示
- `docs/local-demo-guide.md` 提供启动指南
- `docs/demo-script.md` 提供 6 个演示问题和话术
- 当前无法公网访问（未部署）

### 文档

**状态：较完整。**

| 文档 | 路径 | 说明 |
|------|------|------|
| README | `README.md` | 项目全貌、Quick Start、功能列表 |
| 架构 | `docs/architecture.md` | 三端架构、Hybrid Retrieval、Evaluation 设计 |
| API | `docs/api.md` | 接口文档、请求响应格式、curl 示例 |
| Roadmap | `docs/roadmap.md` | 已完成 / 计划中 / 未来功能 |
| 本地演示 | `docs/local-demo-guide.md` | 启动指南、环境变量、健康检查 |
| 演示脚本 | `docs/demo-script.md` | 6 个演示问题、话术、兜底方案 |
| RAG 质量工程 | `docs/rag-quality-engineering.md` | D36-D40 质量优化链路 |
| Daily Logs | `docs/daily-log/` | D1-D41 开发日志（32 个文件，gitignored） |

## 4. 当前会话已完成工作

### Java 后端

**已完成：**

- Spring Boot 项目初始化（pom.xml、application.properties）
- `ChatController`：转发 `/api/chat` 到 Python `/agent/chat`
- `LangGraphAgentController`：转发 `/api/agent/langgraph/chat` 到 Python
- `TraceIdFilter`：全链路 traceId 生成/透传
- `WebConfig`：CORS 配置
- `HealthController` / `AgentHealthController`：健康检查
- Java fallback：Python 不可用时返回 `success=false` + traceId

**涉及文件：**

- `backend-java/src/main/java/com/fantuan/backend/controller/ChatController.java`
- `backend-java/src/main/java/com/fantuan/backend/controller/LangGraphAgentController.java`
- `backend-java/src/main/java/com/fantuan/backend/controller/HealthController.java`
- `backend-java/src/main/java/com/fantuan/backend/filter/TraceIdFilter.java`
- `backend-java/src/main/java/com/fantuan/backend/config/WebConfig.java`

**已知风险：**

- Java 与 Python 之间的 DTO 契约没有强类型约束（手动对齐）
- 没有单元测试

### Python AI / RAG

**已完成：**

- RAG 主链路：`rag_service.py` → `hybrid_retriever.py` → `system_prompt.py` → `llm_service.py`
- Hybrid Retrieval：Faiss + BM25 + RRF 融合（`hybrid_retriever.py`）
- Faiss 语义检索：`faiss_retriever.py`（BAAI/bge-small-zh-v1.5）
- BM25 检索：`bm25_retriever.py`（字符级 n-gram，无外部依赖）
- Keyword 检索：`keyword_retriever.py`（简单关键词匹配）
- Cross Encoder Re-rank：`cross_encoder_reranker.py`（实验模式）
- Query Rewrite：`query_rewriter.py`（规则版，实验模式）
- LangGraph Agent：`langgraph_agent.py`（Safety Guard + 意图路由 + Tool Calling）
- Safety Guard：`safety_guard.py`（5 类风险关键词）
- Evaluation：`eval_retrieval.py`、`eval_generation.py`
- 知识库构建：`build_chunks.py`、`build_embeddings.py`、`build_faiss_index.py`

**涉及文件：**

- `agent-python/app/services/rag_service.py`
- `agent-python/app/retrieval/hybrid_retriever.py`
- `agent-python/app/retrieval/faiss_retriever.py`
- `agent-python/app/retrieval/bm25_retriever.py`
- `agent-python/app/retrieval/keyword_retriever.py`
- `agent-python/app/retrieval/cross_encoder_reranker.py`
- `agent-python/app/retrieval/query_rewriter.py`
- `agent-python/app/prompts/system_prompt.py`
- `agent-python/app/services/llm_service.py`
- `agent-python/app/agents/langgraph_agent.py`
- `agent-python/app/guards/safety_guard.py`
- `agent-python/scripts/eval/eval_retrieval.py`
- `agent-python/scripts/eval/eval_generation.py`

**已知风险：**

- `HF_HUB_OFFLINE=1` 必须设置（国内网络无法访问 HuggingFace）
- `.env` 中的 API Key 不能泄露
- BM25 检索使用字符级 n-gram 分词，不依赖 jieba（architecture.md 中的 jieba 描述已过时）
- Query Rewrite 规则有限，只覆盖部分口语化表达
- LangChain RAG Chain（`langchain_rag_chain.py`）是实验模块，prompt 规则已同步但不作为主链路

### Frontend

**已完成：**

- React 19 + Vite 初始化
- 聊天页面：输入框、发送按钮、消息展示
- RAG/Agent 模式切换
- traceId 展示
- sources 展示（Agent 模式）
- Vite proxy 配置（`/api` → Java 8080）

**涉及文件：**

- `frontend/src/App.jsx`
- `frontend/vite.config.js`
- `frontend/package.json`

**已知风险：**

- 无用户认证
- 无状态管理（每次刷新丢失对话）
- 无错误边界处理
- 无 loading 状态展示

### Docs

**已完成：**

- `README.md`：项目全貌、Quick Start、RAG Quality Engineering、Local Demo、Demo Questions、Project Boundary
- `docs/architecture.md`：三端架构图、模块说明、Hybrid Retrieval 设计
- `docs/api.md`：接口文档、请求响应格式、curl 示例
- `docs/roadmap.md`：已完成 / 计划中 / 未来功能
- `docs/local-demo-guide.md`：本地启动指南、环境变量、健康检查、常见问题
- `docs/demo-script.md`：6 个演示问题、话术、兜底方案、面试口径
- `docs/rag-quality-engineering.md`：D36-D40 质量优化链路
- `docs/daily-log/`：D1-D41 开发日志（32 个文件，gitignored）

**是否需要后续更新：**

- `docs/architecture.md` 中 Hybrid Retrieval 描述提到"jieba 分词"，实际已改为 BM25 字符级 n-gram，需要更新
- `docs/api.md` 中 `expected_answer_keywords` 和 `expected_answer_keyword_groups` 字段未在文档中说明
- `docs/api.md` 中 `failure_type` 字段未在文档中说明

## 5. 当前关键项目口径

以下是项目定位和约束，新会话必须遵守：

1. **当前项目不是已上线生产系统。** 定位是本地可复现 Demo / 生产化改造候选项目。

2. **Re-rank 是实验模式，不得默认启用。** `hybrid_rerank` 模式当前评估集上提升不显著。

3. **Query Rewrite 是实验模式，不得默认启用。** `rewrite_mode=rule` 当前规则有限。

4. **当前不做公网服务器部署。** 涉及 API Key 安全、限流、Nginx、进程守护、成本控制。

5. **线上面试采用本地启动 + 屏幕共享。** 线下面试通过 README、demo-script、评估结果和架构讲解。

6. **Python AI 服务是内部能力层，前端不应直接调用 Python。** 所有请求必须经过 Java 后端。

7. **权限判断必须在 Java 后端完成，不能相信前端 role。** 当前无权限系统，但架构上 Java 是权限层。

8. **100% 评估通过率不代表 RAG 完全可靠。** 基于当前 38 个 eval cases，知识库规模较小。

9. **不要把实验功能写成 production-ready。** hybrid_rerank、rewrite_mode=rule、LangChain RAG Chain 都是实验模块。

10. **不要泄露 API Key。** `.env` 不提交、文档不暴露、前端不硬编码。

## 6. 当前 API / 服务边界

### 前端 → Java

| 接口 | 说明 |
|------|------|
| `POST /api/chat` | 稳定 RAG 主链路 |
| `POST /api/agent/langgraph/chat` | 实验 Agent 链路 |
| `GET /api/health` | Java 健康检查 |
| `GET /api/agent/health` | Python 健康检查（Java 代理） |

### Java → Python

| 接口 | 说明 |
|------|------|
| `POST /agent/chat` | RAG 问答 |
| `POST /agent/langgraph/chat` | Agent 问答 |
| `GET /agent/health` | Python 健康检查 |

### Python 内部链路

**稳定 RAG 链路（`/agent/chat`）：**

```
rag_service.process_chat()
  → query_rewriter.rewrite_query()     # 实验模式，none 时跳过
  → hybrid_retriever.retrieve()         # Faiss + BM25 + RRF
  → system_prompt.build_rag_prompt()    # 拼接知识片段 + 规则
  → llm_service.call_llm()             # DeepSeek API
  → ChatResponse
```

**实验 Agent 链路（`/agent/langgraph/chat`）：**

```
langgraph_agent.run_langgraph_agent()
  → safety_node → check_user_query_safety()
  → router_node → 规则路由（rag / eval / refuse）
  → rag_node → rag_answer_tool() → LangChain RAG
  → eval_node → eval_report_tool() → 读取评估报告
  → refuse_node → 安全拒答文案
  → AgentResponse
```

### traceId 透传

```
Frontend: crypto.randomUUID() → X-Trace-Id header
Java: TraceIdFilter → MDC + request.setAttribute + 透传 header
Python: trace_id_middleware → request.state.trace_id + 透传 header
响应: header + body 都包含 traceId
```

### Java fallback

Python 不可用时，Java 返回：
```json
{"answer": "当前 AI 服务暂时不可用，请稍后重试。", "success": false, "traceId": "..."}
```

## 7. 当前已知问题和风险点

### 启动风险

| 风险 | 说明 | 影响 |
|------|------|------|
| HuggingFace 离线 | 国内网络无法访问 HuggingFace，必须设置 `HF_HUB_OFFLINE=1` | Python 服务启动失败 |
| Faiss 索引缺失 | 如果 `data/processed/faiss.index` 不存在，Faiss 检索不可用 | RAG 降级为纯关键词检索 |
| API Key 缺失 | `.env` 中 `DEEPSEEK_API_KEY` 未配置 | LLM 调用失败，retrieval eval 仍可运行 |

### 环境变量风险

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | 必须配置，否则 LLM 不可用 |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-chat` |
| `HF_HUB_OFFLINE` | 国内网络必须设为 `1` |
| `RETRIEVAL_MODE` | 默认 `hybrid`，可选 `vector` / `hybrid_rerank` |
| `REWRITE_MODE` | 默认 `none`，可选 `rule` |

### API Key 风险

- `.env` 文件在 `.gitignore` 中，但本地文件包含真实 Key
- 文档中的 `your_api_key_here` 是占位符，不是真实 Key
- 前端代码不包含 Key
- **风险：** 如果 `.gitignore` 被误改，Key 可能被提交

### Python 模型 / HuggingFace 离线加载风险

- 模型缓存在 `~/.cache/huggingface/hub/`
- `BAAI/bge-small-zh-v1.5` 和 `BAAI/bge-reranker-base` 都需要本地缓存
- 如果缓存被清理，需要重新下载（需要能访问 HuggingFace 的网络）
- `HF_HUB_OFFLINE=1` 已加入 `.env`

### Java / Python DTO 契约不一致风险

- Java `ChatRequest` 和 Python `ChatRequest` 手动对齐，无共享 schema
- Java `ChatResponse` 和 Python `ChatResponse` 手动对齐
- 如果一端改了字段名/类型，另一端不会自动报错
- **建议：** 后续考虑 OpenAPI spec 或共享 DTO

### 前端展示与接口字段不一致风险

- 前端直接读取 JSON 字段，无类型校验
- 如果后端返回字段名变化，前端不会报错但会显示为空
- **建议：** 后续考虑 TypeScript + 接口类型定义

### Evaluation 结果被夸大风险

- 当前 100% 通过率基于 38 个 eval cases
- 知识库只有 33 个文档片段
- 没有 hard negative 测试
- 没有对抗样本
- **风险：** 面试时如果说"100% 召回率"会误导面试官

### 多会话协作时可能互相覆盖的文件

| 文件 | 风险 |
|------|------|
| `agent-python/app/prompts/system_prompt.py` | 多人改 Prompt 会冲突 |
| `agent-python/app/retrieval/hybrid_retriever.py` | 改检索逻辑会冲突 |
| `data/eval/rag_eval_cases.json` | 改 eval cases 会影响评估结果 |
| `data/eval/reports/*` | eval 产物，不应被多会话同时写入 |
| `README.md` | 多人改 README 会冲突 |

## 8. 不建议继续做的事项

1. **不建议继续盲目新增功能。** 当前功能闭环已完整，应优先质量而非数量。

2. **不建议直接上服务器部署。** 涉及 API Key 安全、限流、Nginx、进程守护、成本控制，需要专门的 DevOps 规划。

3. **不建议把实验功能默认启用。** `hybrid_rerank` 和 `rewrite_mode=rule` 仍是实验模式。

4. **不建议多个会话同时修改同一模块。** 特别是 `system_prompt.py`、`hybrid_retriever.py`、`rag_eval_cases.json`。

5. **不建议直接在 main 分支开发。** 应使用 feature 分支 + squash merge 工作流。

6. **不建议把个人项目包装成生产级已上线系统。** 面试时应诚实表达项目定位。

7. **不建议删除或修改 eval baseline 除非有充分理由。** baseline 是回归检测的基准。

8. **不建议修改知识库文档内容。** 知识库文档是 eval cases 的基础，改动会影响评估结果。

## 9. 建议后续多 Agent 分工

### 架构 Owner

**职责：** 全局视角，协调各会话，维护架构文档。

- 盘点当前架构和模块边界
- 维护 `docs/agent-collaboration/` 协作文档
- 建立 Agent Registry 和 Task Board
- 审核各会话的修改是否符合架构约束
- 更新 `docs/architecture.md`（修正 jieba → BM25 描述）

### 全栈开发工程师

**职责：** Java 后端 + React 前端。

- 盘点 Java 后端接口和 DTO 契约
- 盘点前端页面能力和不足
- 补充 Java 单元测试
- 前端增加 loading 状态、错误边界
- 统一 Java 错误码

### AI/RAG 工程师

**职责：** Python AI 服务、RAG 链路、Evaluation。

- 盘点 Python 各模块状态
- 补充 Python 单元测试
- 扩充 eval cases（更多场景、对抗样本）
- 优化 Query Rewrite 规则
- 更新 `docs/api.md` 中缺失的字段说明（keyword_groups、failure_type）

### QA

**职责：** 验证三端可用性，制定 smoke test。

- 制定 smoke test 清单（三端启动、RAG 问答、Agent 问答、安全拒答）
- 验证 eval 流程可复跑
- 验证本地演示流程
- 检查文档与实际行为是否一致

### 安全 Review

**职责：** 审查安全边界。

- 检查 `.env` 和 API Key 是否有泄露风险
- 检查 Safety Guard 覆盖范围
- 检查 Java 权限边界（当前无权限系统，确认架构设计）
- 检查 Python 输入校验

### DevOps

**职责：** 部署和运维（当前阶段不介入）。

- **当前不建议介入。** 项目尚未达到部署标准。
- 后续需要时：Docker Compose、Nginx、进程守护、API Key 管理、限流

## 10. 下一步建议任务

| 任务 ID | 任务 | 优先级 | 建议负责 |
|---------|------|--------|---------|
| TASK-001 | 创建 agent-collaboration 协作文档（Agent Registry、Task Board） | 高 | 架构 Owner |
| TASK-002 | 建立 Agent Registry（记录各会话角色和职责） | 高 | 架构 Owner |
| TASK-003 | 建立 Task Board（记录待办、进行中、已完成） | 高 | 架构 Owner |
| TASK-004 | 全栈会话盘点 Java + Frontend（接口、DTO、页面能力） | 中 | 全栈开发 |
| TASK-005 | AI/RAG 会话盘点 Python AI 层（模块状态、已知问题） | 中 | AI/RAG 工程师 |
| TASK-006 | QA 会话制定 smoke test（三端启动、核心功能验证） | 中 | QA |
| TASK-007 | 安全会话审查权限边界（API Key、Safety Guard、输入校验） | 中 | 安全 Review |
| TASK-008 | 修正 architecture.md 中 Hybrid Retrieval 描述（jieba → BM25） | 低 | 架构 Owner |
| TASK-009 | 更新 api.md 补充 keyword_groups、failure_type 字段说明 | 低 | AI/RAG 工程师 |
| TASK-010 | 提交 docs/local-demo-guide.md 的 HF_HUB_OFFLINE 改动 | 低 | 任意会话 |

## 11. 给新会话的启动建议

### 新架构 Owner

1. 先读 `README.md` 了解项目全貌
2. 读 `docs/architecture.md` 了解三端架构（注意 jieba 描述已过时）
3. 读 `docs/roadmap.md` 了解已完成和计划中功能
4. 读本文档（`legacy-dev-session-handoff.md`）了解当前状态
5. 创建 `docs/agent-collaboration/agent-registry.md`
6. 创建 `docs/agent-collaboration/task-board.md`
7. 不要修改 Python / Java / Frontend 代码
8. 不要修改 eval cases 或 baseline
9. 第一件事：建立协作文档框架

### 新全栈开发工程师

1. 先读 `README.md` 和 `docs/api.md`
2. 读 `backend-java/src/main/java/com/fantuan/backend/controller/` 盘点接口
3. 读 `frontend/src/App.jsx` 盘点前端能力
4. 不要修改 Python AI 服务代码
5. 不要修改 eval 逻辑
6. 不要直接在 main 分支开发
7. 第一件事：盘点 Java DTO 和前端字段的一致性

### 新 AI/RAG 工程师

1. 先读 `README.md` 和 `docs/rag-quality-engineering.md`
2. 读 `agent-python/app/retrieval/hybrid_retriever.py` 了解检索架构
3. 读 `agent-python/app/prompts/system_prompt.py` 了解 Prompt 设计
4. 读 `agent-python/scripts/eval/` 了解评估体系
5. 不要修改 Java 后端代码
6. 不要修改前端代码
7. 不要修改知识库文档
8. 不要默认启用 `hybrid_rerank` 或 `rewrite_mode=rule`
9. 第一件事：盘点 Python 各模块状态和已知问题

### 新 QA 会话

1. 先读 `README.md` 和 `docs/local-demo-guide.md`
2. 读 `docs/demo-script.md` 了解演示流程
3. 尝试本地启动三端服务
4. 运行 `uv run python scripts/eval/run_rag_eval.py` 验证评估流程
5. 不要修改任何代码
6. 不要修改 eval cases
7. 第一件事：制定 smoke test 清单并执行

### 新安全 Review 会话

1. 先读 `README.md` 和 `docs/architecture.md`
2. 检查 `.env` 是否在 `.gitignore` 中
3. 读 `agent-python/app/guards/safety_guard.py` 了解安全边界
4. 读 `agent-python/app/main.py` 检查输入校验
5. 不要修改任何代码
6. 第一件事：审查 API Key 管理和 Safety Guard 覆盖范围
