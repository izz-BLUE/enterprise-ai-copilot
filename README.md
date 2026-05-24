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
| 检索评估 | `eval_retrieval.py` | source_hit / keyword_hit / final_pass_rate | `retrieval_eval_report.json` |
| 生成评估 | `eval_generation.py` | answer 关键词命中 / LLM 成功率 / pass_rate | `generation_eval_report.json` |

- 检索评估不调用 LLM，零 token 消耗
- 生成评估调用 LLM 但使用关键词规则判断，不引入 LLM-as-judge
- 支持文本归一化（去空格、全角转半角），减少格式误判
- JSON 报告支持 CI 接入、回归测试和质量追踪

### 7. 已验证的测试集

当前 8 个测试用例覆盖请假、年假、婚假、产假、病假材料、迟到早退处罚、旷工解除合同等场景。

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
│   └── scripts/               # build_chunks, build_embeddings, build_faiss_index, eval_*
├── data/
│   ├── hr/ bank/ it/          # 知识库源文档
│   ├── processed/             # chunks.json, faiss.index, faiss_metadata.json
│   └── eval/                  # rag_eval_cases.json, reports/
└── docs/                      # 项目文档, 架构说明, 每日日志
```

---

## 链路说明

### 离线构建

```
Markdown → build_chunks.py → chunks.json
         → build_embeddings.py → embeddings.json
         → build_faiss_index.py → faiss.index + faiss_metadata.json
```

### 在线问答

```
用户问题 → Java /api/chat → Python /agent/chat
         → Hybrid Retrieval → TopK Chunks → RAG Prompt
         → DeepSeek → Answer → Java → 用户
```

### 评估

```
rag_eval_cases.json → eval_retrieval.py  → retrieval_eval_report.json
rag_eval_cases.json → eval_generation.py → generation_eval_report.json
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
