# Enterprise AI Copilot

面向企业内部知识库问答场景的 AI 应用后端项目。

Java Spring Boot 作为业务入口，Python FastAPI 作为 AI 服务引擎。
支持文档切片、Embedding、向量检索、Hybrid Retrieval、RAG Prompt、LLM 回答和两层评估。

---

## 已完成能力

### 1. Java + Python 双服务架构

- Java Spring Boot 提供统一 `/api/chat` 接口，负责路由、请求转发、统一响应和异常兜底
- Python FastAPI 提供 `/agent/chat` AI 服务，负责 RAG 检索、Prompt 构造和 LLM 调用
- 两端通过 HTTP JSON 通信

### 2. DeepSeek 大模型接入

- 通过 OpenAI SDK 兼容模式调用 DeepSeek
- system prompt 与 user message 分离
- 支持企业 AI 助手角色约束
- 知识库无结果时拒答，减少幻觉

### 3. 文档切片 (Chunking)

- 基于段落 + 字符长度的切片策略
- 支持 chunk overlap
- 短段落合并，避免碎片化
- 标题与正文合并，避免标题孤岛
- 输出 `chunks.json` 作为中间产物

### 4. Embedding 与 Faiss 向量索引

- 使用 `BAAI/bge-small-zh-v1.5` 生成 512 维中文 Embedding
- 使用 `faiss-cpu` 构建 `IndexFlatIP`
- L2 normalize 后通过 inner product 近似 cosine similarity
- 输出 `faiss.index` + `faiss_metadata.json`

### 5. Hybrid Retrieval

```
Faiss Semantic Retrieval  +  Keyword Retrieval  →  Merge  →  Deduplicate  →  TopK
```

Faiss 负责语义召回，Keyword 检索负责精确关键词补充，去重后截取 TopK。

### 6. RAG Evaluation（两层评估）

| 层级 | 脚本 | 评估内容 | 输出 |
|------|------|----------|------|
| 检索评估 | `scripts/eval/eval_retrieval.py` | source_hit / keyword_hit / final_pass_rate | `reports/retrieval_eval_report.json` |
| 生成评估 | `scripts/eval/eval_generation.py` | answer 关键词命中 / flaky 检测 / pass_rate | `reports/generation_eval_report.json` |
| 回归检查 | `scripts/eval/compare_eval_reports.py` | baseline vs current 对比 | 控制台 + 退出码 |
| 一键评估 | `scripts/eval/run_rag_eval.py` | 串联检索+生成+回归 | 控制台汇总 |

- 检索评估不调用 LLM，零 token 消耗
- 生成评估支持 retry 机制，自动标记 flaky case
- 支持文本归一化（去空格、全角转半角），减少格式误判
- JSON 报告 + baseline 对比，支持 CI 质量门禁

### 7. 已验证的测试集

当前 8 个测试用例覆盖请假、年假、婚假、产假、病假材料、迟到早退处罚、旷工解除合同等场景。

### 8. LangChain RAG Chain（实验性模块）

`app/chains/langchain_rag_chain.py` 提供 `answer_with_langchain_rag()` 函数，使用 LangChain ChatPromptTemplate + ChatOpenAI 封装 RAG 问答链路。

```
python agent-python/scripts/experiments/langchain_rag_demo.py "病假需要提供哪些材料？"
```

- 复用现有 `hybrid_retriever` 做检索，LangChain 负责 Prompt 模板和 LLM 调用
- 当前 /agent/chat 主流程仍使用手写 RAG（`rag_service.py`），未替换
- 此模块作为实验性可复用封装，用于对比手写实现和框架实现的差异

### 9. LangGraph Agent（实验性模块）

`app/agents/langgraph_agent.py` 实现 safety → router → (rag | eval | refuse) 状态图。

- 集成 Safety Guard 输入安全检查（5 类风险规则）
- 规则路由：自动区分 RAG 问答、评估查询、安全拒答
- 暴露为 `POST /agent/langgraph/chat` 和 Java `POST /api/agent/langgraph/chat`
- 与 `/agent/chat` **并行运行**，不替换原 RAG 主链路

### 10. Java 代理接口

- `POST /api/chat` — 代理 Python `/agent/chat`（RAG 主链路）
- `POST /api/agent/langgraph/chat` — 代理 Python `/agent/langgraph/chat`（Agent 链路）
- Python 服务地址统一配置：`python.agent.base-url=http://localhost:8000`

---

## 技术栈

| 层 | 技术 |
|---|------|
| 业务后端 | Java 17, Spring Boot 3.x, RestTemplate, Maven |
| AI 服务 | Python 3.11, FastAPI, Pydantic, Uvicorn |
| 大模型 | DeepSeek V4 (OpenAI SDK compatible API) |
| Embedding | BAAI/bge-small-zh-v1.5 (512-dim) |
| 向量检索 | faiss-cpu, IndexFlatIP |
| 关键词检索 | jieba 分词 + 关键词匹配 |
| RAG 评估 | Python 脚本, JSON 报告 |

---

## 项目结构

```
enterprise-ai-copilot/
├── backend-java/              # Java Spring Boot 业务系统
│   └── src/main/java/com/enterprise/
├── agent-python/              # Python FastAPI AI 服务
│   ├── app/
│   │   ├── core/              # config, 日志
│   │   ├── prompts/           # system prompt, RAG prompt 构造
│   │   ├── retrieval/         # faiss_retriever, keyword_retriever, hybrid_retriever
│   │   ├── schemas/           # ChatRequest, ChatResponse, AgentResponse
│   │   ├── services/          # rag_service, llm_service
│   │   ├── chains/            # langchain_rag_chain — LangChain RAG 封装
│   │   ├── tools/             # rag_answer_tool, eval_report_tool
│   │   ├── agents/            # langgraph_agent — LangGraph 状态图
│   │   └── guards/            # safety_guard — 输入安全边界控制
│   └── scripts/               # build/ 构建, eval/ 评估, experiments/ 实验
├── data/
│   ├── hr/ bank/ it/          # 知识库源文档
│   ├── processed/             # chunks.json, faiss.index, faiss_metadata.json
│   └── eval/                  # rag_eval_cases.json, reports/
└── docs/                      # 项目文档, 架构说明, 每日日志
```

---

## 快速启动

### Python 服务

```bash
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### Java 服务

```bash
cd backend-java
./mvnw spring-boot:run
```

Java 调用 Python 的地址配置在 `application.properties`：
```properties
python.agent.base-url=http://localhost:8000
```

---

## 两条聊天链路

| 特性 | `/api/chat` | `/api/agent/langgraph/chat` |
|------|------------|---------------------------|
| Python 接口 | `/agent/chat` | `/agent/langgraph/chat` |
| 实现方式 | 手写 RAG（rag_service） | LangGraph Agent 状态图 |
| Safety Guard | 无 | 有（5 类风险规则） |
| 意图路由 | 无 | 有（rag / eval / refuse） |
| Tool Calling | 无 | 有（rag_answer_tool + eval_report_tool） |
| 稳定性 | 稳定主链路 | 实验链路 |
| 接口文档 | [docs/api.md](docs/api.md) | [docs/api.md](docs/api.md) |

**`/api/chat` 是稳定 RAG 主链路，`/api/agent/langgraph/chat` 是 Agent 实验链路。采用并行接口便于灰度验证。**

---

## 链路说明

### 离线构建

```bash
cd agent-python
uv run python scripts/build/build_chunks.py
uv run python scripts/build/build_embeddings.py
uv run python scripts/build/build_faiss_index.py
```

### 在线问答

```
用户问题 → Java /api/chat → Python /agent/chat
         → Hybrid Retrieval → TopK Chunks → RAG Prompt
         → DeepSeek → Answer → Java → 用户
```

### 评估

```bash
cd agent-python

# 一键运行全部评估
uv run python scripts/eval/run_rag_eval.py

# 初始化 baseline（需先确保当前报告全部通过）
uv run python scripts/eval/update_eval_baseline.py

# 评估 + 质量门禁（对比 baseline）
uv run python scripts/eval/run_rag_eval.py --with-baseline

# 实验脚本
uv run python scripts/experiments/langgraph_agent_demo.py "病假需要提供哪些材料？"
uv run python scripts/experiments/tool_calling_demo.py "当前RAG评估通过率是多少？"
```

---

## 后续规划

以下功能尚未实现，仅作为后续扩展方向：

- BM25 检索 / RRF 排名融合
- Cross Encoder Re-rank
- Query Rewrite
- 多轮对话上下文管理
- LLM 自动 Tool Calling（当前为规则路由）
- Qdrant / Milvus 向量数据库
- 文档上传与知识库管理
- 用户权限控制
- 审计日志
- Docker Compose 一键部署
- CI RAG 回归测试
