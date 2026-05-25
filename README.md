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
│   │   ├── schemas/           # ChatRequest, ChatResponse
│   │   └── services/          # rag_service, llm_service
│   └── scripts/               # build/ 构建, eval/ 评估, experiments/ 早期实验
├── data/
│   ├── hr/ bank/ it/          # 知识库源文档
│   ├── processed/             # chunks.json, faiss.index, faiss_metadata.json
│   └── eval/                  # rag_eval_cases.json, reports/
└── docs/                      # 项目文档, 架构说明, 每日日志
```

---

## 链路说明

### 离线构建

```bash
python agent-python/scripts/build/build_chunks.py
python agent-python/scripts/build/build_embeddings.py
python agent-python/scripts/build/build_faiss_index.py
```

### 在线问答

```
用户问题 → Java /api/chat → Python /agent/chat
         → Hybrid Retrieval → TopK Chunks → RAG Prompt
         → DeepSeek → Answer → Java → 用户
```

### 评估

```bash
# 一键运行全部评估
python agent-python/scripts/eval/run_rag_eval.py

# 初始化 baseline（需先确保当前报告全部通过）
python agent-python/scripts/eval/update_eval_baseline.py

# 评估 + 质量门禁（对比 baseline）
python agent-python/scripts/eval/run_rag_eval.py --with-baseline

# 单独运行
python agent-python/scripts/eval/eval_retrieval.py
python agent-python/scripts/eval/eval_generation.py
python agent-python/scripts/eval/compare_eval_reports.py baseline.json current.json
```

---

## 后续规划

以下功能尚未实现，仅作为后续扩展方向：

- BM25 检索 / RRF 排名融合
- Cross Encoder Re-rank
- Query Rewrite
- 多轮对话上下文管理
- LangChain / LangGraph 任务编排
- Qdrant / Milvus 向量数据库
- 文档上传与知识库管理
- 用户权限控制
- 审计日志
- Docker Compose 一键部署
- CI RAG 回归测试
