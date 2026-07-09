# 架构说明

## 项目定位

Enterprise AI Copilot 是一个**企业知识库 AI 应用后端**项目，采用 Java Spring Boot + Python FastAPI 双服务架构，支持 RAG 检索增强生成问答。

## 总体架构

```mermaid
flowchart TD
    subgraph Frontend ["Frontend (React + Vite :5173)"]
        UI[App.jsx]
    end

    subgraph Java ["Java Spring Boot :8080"]
        HC[HealthController]
        CC[ChatController]
        LAC[LangGraphAgentController]
        TID[TraceIdFilter]
    end

    subgraph Python ["Python FastAPI :8000"]
        MW[trace_id_middleware]
        EP1[/agent/chat]
        EP2[/agent/langgraph/chat]
        SG[Safety Guard]
        RT[Router]
    end

    subgraph RAG ["RAG 管道"]
        HR[Hybrid Retriever]
        FR[Faiss Semantic]
        KR[Keyword Retrieval]
        PP[Prompt Builder]
        LLM[DeepSeek LLM]
    end

    subgraph Agent ["LangGraph Agent"]
        SN[safety_node]
        RN[router_node]
        RAGN[rag_node]
        EN[eval_node]
        REFN[refuse_node]
    end

    subgraph KB ["知识库离线构建"]
        MD[Markdown 文档]
        CK[Chunking]
        EM[BGE Embedding]
        FI[FAISS Index]
    end

    subgraph Eval ["Evaluation"]
        RE[Retrieval Eval]
        GE[Generation Eval]
        BL[Baseline Regression]
    end

    UI -->|POST /api/chat| CC
    UI -->|POST /api/agent/langgraph/chat| LAC
    TID -->|X-Trace-Id| CC
    TID -->|X-Trace-Id| LAC

    CC -->|HTTP + X-Trace-Id| EP1
    LAC -->|HTTP + X-Trace-Id| EP2

    EP1 --> HR
    HR --> FR
    HR --> KR
    HR --> PP
    PP --> LLM

    EP2 --> SN
    SN --> RN
    RN -->|rag| RAGN
    RN -->|eval| EN
    RN -->|refuse| REFN
    RAGN --> HR

    MD --> CK --> EM --> FI
    FI -.->|在线检索| FR
```

## 项目模块

| 模块 | 目录 | 说明 |
|------|------|------|
| backend-java | `backend-java/` | Java Spring Boot 业务系统，提供对外 API 并代理 Python 接口 |
| agent-python | `agent-python/` | Python FastAPI AI 服务，包含 RAG、Agent、Tools、Safety Guard |
| knowledge-base | `data/hr/ bank/ it/` | 企业知识库 Markdown 文档 |
| evaluation | `data/eval/` | RAG 评估测试集、报告和 baseline |
| frontend | `frontend/` | React + Vite 前端演示页面 |
| docs | `docs/` | 项目文档、架构说明、接口文档 |

## 三端架构

| 层 | 技术 | 端口 | 职责 |
|---|------|------|------|
| 前端 | React + Vite | 5173 | 用户交互、模式切换、traceId 展示 |
| 业务网关 | Java Spring Boot | 8080 | 统一入口、traceId 管理、异常兜底、CORS |
| AI 引擎 | Python FastAPI | 8000 | RAG 检索、Prompt 构造、LLM 调用、Agent 编排 |

## Java Backend 职责

- **TraceIdFilter**：统一生成/读取 traceId，存入 SLF4J MDC 和 request attribute，设置响应头
- **ChatController**：转发 `/api/chat` 到 Python `/agent/chat`，透传 traceId
- **LangGraphAgentController**：转发 `/api/agent/langgraph/chat` 到 Python `/agent/langgraph/chat`，透传 traceId
- **HealthController / AgentHealthController**：健康检查
- **WebConfig**：CORS 配置（可配置白名单 `cors.allowed-origins`），暴露 `X-Trace-Id` 响应头
- **RestClientConfig**：RestTemplate 超时配置（`connect-timeout` 3s，`read-timeout` 30s）
- **ChatRequest**：输入长度校验（`@Size(max=2000)`）
- **GlobalExceptionHandler**：全局异常处理，统一错误响应

## Python AI Service 职责

- **trace_id_middleware**：接收/生成 traceId，写入 `request.state`，设置响应头
- **rag_service**：RAG 管道（检索 → 拼 Prompt → 调 LLM → 返回）
- **langgraph_agent**：LangGraph 状态图编排（safety → router → rag/eval/refuse）
- **safety_guard**：基于关键词的输入安全检查（5 类风险）
- **hybrid_retriever**：支持 vector / hybrid / hybrid_rerank 三种检索模式
  - `vector`：Faiss 语义检索 + keyword 检索合并去重
  - `hybrid`（默认）：Faiss + BM25 + RRF 融合排序
  - `hybrid_rerank`（实验）：Hybrid 候选召回 + Cross Encoder 精排
- **query_rewriter**：规则版查询重写（实验模式，`rewrite_mode=rule`）
- **cross_encoder_reranker**：Cross Encoder 精排（实验模式，`hybrid_rerank`）
- **llm_service**：通过 OpenAI SDK 调用 DeepSeek API

## 两条聊天链路

### 链路一：/api/chat（稳定 RAG 主链路）

```
POST /api/chat
  → Java ChatController（读取 traceId，透传 X-Trace-Id）
    → @Size(max=2000) 输入长度校验
    → RestTemplate 调 Python（connect-timeout 3s, read-timeout 30s）
    → Python POST /agent/chat
      → MAX_MESSAGE_LENGTH 兜底校验（默认 2000）
      → rag_service.process_chat()
        → safety_guard.check_user_query_safety()  # Phase 3: 规则版 Safety Guard 前置检查
        → query_rewriter.rewrite_query()           # 实验模式，none 时跳过
        → hybrid_retriever.retrieve()
          ├── faiss_retriever（BGE embedding 语义检索）
          └── bm25_retriever（字符级 n-gram BM25 检索）
        → RRF 融合排序（默认 hybrid 模式）→ TopK=3
        → build_rag_prompt()
        → llm_service.call_llm()                   # LLM_TIMEOUT 超时控制（默认 30s）
          → DeepSeek API
        → ChatResponse（含 traceId）
```

**特点**：手写全链路，不依赖 LangChain/LangGraph，稳定可靠。Phase 3 新增 Safety Guard 前置检查，高风险问题直接拒答不进入检索。

> **注意：** Safety Guard 是规则版基础防护（5 类风险关键词匹配），不是完整安全系统。

### 链路二：/api/agent/langgraph/chat（Agent 实验链路）

```
POST /api/agent/langgraph/chat
  → Java LangGraphAgentController
    → Python POST /agent/langgraph/chat
      → run_langgraph_agent()
        → StateGraph.invoke()
          ├── safety_node
          │     └── check_user_query_safety()
          │           ├── unsafe → route=refuse
          │           └── safe → 继续
          ├── router_node
          │     ├── 评估类关键词 → route=eval
          │     └── 其他 → route=rag
          ├── rag_node
          │     └── rag_answer_tool()
          │           └── answer_with_langchain_rag()
          ├── eval_node
          │     └── eval_report_tool()
          │           └── read evaluation reports
          └── refuse_node
                └── 返回安全拒答文案
```

**特点**：LangGraph 状态图编排，规则路由，Safety Guard + Tools + 多分支。

## 离线知识库构建流程

```
data/hr/*.md, data/it/*.md, data/bank/*.md
  → build_chunks.py（段落切片 + 短段落合并 + 长段落 overlap 拆分）
    → data/processed/chunks.json
  → build_embeddings.py（BGE embedding 编码）
    → data/processed/embeddings.json
  → build_faiss_index.py（FAISS 索引构建）
    → data/processed/faiss.index + faiss_metadata.json
```

## Hybrid Retrieval 设计

支持三种检索模式：

**hybrid 模式（默认）：**
```
用户问题
  ├─→ Faiss Semantic Retrieval（向量余弦相似度）
  └─→ BM25 Retrieval（字符级 n-gram，无外部依赖，对中文友好）
       ↓
  RRF（Reciprocal Rank Fusion）融合排序
       ↓
  TopK=3 → 传给 LLM
```

**vector 模式：**
```
用户问题
  ├─→ Faiss Semantic Retrieval
  └─→ Keyword Retrieval（简单关键词匹配）
       ↓
  按 chunk id 合并去重 → TopK=3
```

**hybrid_rerank 模式（实验）：**
```
用户问题
  → Hybrid Retrieval → Top10 候选
  → Cross Encoder 精排（BAAI/bge-reranker-base）
  → TopK=3
```

**Query Rewrite（实验模式）：**
```
original_query → query_rewriter → rewritten_query → retrieval
                                                  ↓
                                    prompt 使用 original_query
```

> `hybrid_rerank` 和 `rewrite_mode=rule` 是实验模式，不建议默认启用。

## Evaluation 架构

### Retrieval Evaluation（零 token 消耗）

检查 TopK 检索结果是否包含预期来源和预期关键词。

- answerable case：检查 `source_hit` + `keyword_hit`
- no-answer case：SKIP，不判 fail，只记录检索结果

### Generation Evaluation（调用 LLM）

检查 LLM 最终回答是否包含预期关键词或正确拒答。

- answerable case：检查 `expected_answer_keywords` 命中
- no-answer case：检查是否包含拒答关键词（"未找到"、"当前知识库"等）
- flaky 机制：第一次 FAIL 后 retry 一次，区分随机波动和稳定失败

### Baseline Regression

`compare_eval_reports.py` 对比 baseline 和 current report，判断是否有退化。

- `exit 0` = NO REGRESSION
- `exit 1` = REGRESSION DETECTED

## traceId 全链路透传

> **Phase 3 Batch 3-A 变更：** Java 入口统一生成服务端 traceId，不再信任客户端传入的 `X-Trace-Id`。

```
Frontend: 发送请求（X-Trace-Id 可选，不被信任）
  ↓
Java TraceIdFilter: 忽略客户端 X-Trace-Id，统一生成 UUID
  → MDC + request.setAttribute + 响应头 X-Trace-Id
  ↓
Java → Python: X-Trace-Id（服务端生成，透传）
  ↓
Python middleware: 读取 → request.state.trace_id + 响应头
  → JSON: { "traceId": "..." }
Frontend: 展示 traceId 标签
```

客户端传入的非法 traceId（含控制字符、超长、非 UUID 格式）会被丢弃，Java 重新生成。

## 异常兜底设计

| 场景 | 处理 |
|------|------|
| Python 服务不可用 | Java 返回 `success=false`，traceId 仍然存在 |
| Java → Python 超时 | RestTemplate 超时（3s 连接 / 30s 读取），Java 返回兜底响应 |
| LLM 调用超时 | Python `llm_service` 捕获 `APITimeoutError`，返回 `success=false` |
| LLM 调用失败 | Python rag_service 返回 `success=false`，日志记录异常 |
| 输入过长 | Java `@Size(max=2000)` 拦截 + Python `MAX_MESSAGE_LENGTH` 兜底 |
| 知识库无检索结果 | Prompt 兜底："当前知识库暂无相关信息，不要编造" |
| 安全问题输入 | Safety Guard 拦截，返回安全拒答文案 |
| Agent 异常 | Python endpoint catch Exception，返回 `success=false` |

> **Phase 3 Batch 3-A 变更：** 异常响应中的 `reason` 字段不再暴露底层异常详情（如 `e.getMessage()` / `str(e)`）。用户看到稳定通用文案，服务端日志保留完整异常堆栈和 traceId，用户通过 traceId 反馈问题，服务端通过日志排查。

## Python 模块一览

```
agent-python/app/
├── core/          # config.py — 环境变量、路径、常量
├── retrieval/     # faiss_retriever, keyword_retriever, bm25_retriever, hybrid_retriever, query_rewriter, cross_encoder_reranker
├── services/      # rag_service.py, llm_service.py
├── prompts/       # system_prompt.py, build_rag_prompt()
├── schemas/       # ChatRequest, ChatResponse, AgentResponse
├── chains/        # langchain_rag_chain.py — LangChain RAG 封装
├── tools/         # rag_answer_tool, eval_report_tool — LangChain @tool
├── agents/        # langgraph_agent.py — LangGraph Agent 状态图
├── guards/        # safety_guard.py — 输入安全边界控制
└── main.py        # FastAPI 应用入口 + trace_id_middleware
```

## 配置说明

```properties
# Java → Python 服务地址
python.agent.base-url=http://localhost:8000

# Java 日志格式（含 traceId）
logging.pattern.console=%d{HH:mm:ss.SSS} [%X{traceId}] %-5level %logger{36} - %msg%n
```

## 当前架构边界（未生产化）

以下能力尚未实现，属于 Roadmap 范畴：

- 用户认证与权限控制
- 文档上传与知识库管理
- 多租户隔离
- 审计日志
- Docker Compose 部署
- CI/CD 集成
- 监控告警
- 多模型配置
