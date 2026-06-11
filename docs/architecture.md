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
- **WebConfig**：CORS 配置，暴露 `X-Trace-Id` 响应头

## Python AI Service 职责

- **trace_id_middleware**：接收/生成 traceId，写入 `request.state`，设置响应头
- **rag_service**：RAG 管道（检索 → 拼 Prompt → 调 LLM → 返回）
- **langgraph_agent**：LangGraph 状态图编排（safety → router → rag/eval/refuse）
- **safety_guard**：基于关键词的输入安全检查（5 类风险）
- **hybrid_retriever**：Faiss 语义检索 + 关键词检索，合并去重取 TopK
- **llm_service**：通过 OpenAI SDK 调用 DeepSeek API

## 两条聊天链路

### 链路一：/api/chat（稳定 RAG 主链路）

```
POST /api/chat
  → Java ChatController（读取 traceId，透传 X-Trace-Id）
    → Python POST /agent/chat
      → rag_service.process_chat()
        → hybrid_retriever.retrieve()
          ├── faiss_retriever（BGE embedding 语义检索）
          └── keyword_retriever（jieba 分词关键词检索）
        → Merge + Dedup + TopK=3
        → build_rag_prompt()
        → llm_service.call_llm()
          → DeepSeek V4
        → ChatResponse（含 traceId）
```

**特点**：手写全链路，不依赖 LangChain/LangGraph，稳定可靠。

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

```
用户问题
  ├─→ Faiss Semantic Retrieval（向量余弦相似度，能搜到语义相近的 chunk）
  └─→ Keyword Retrieval（jieba 分词 + n-gram 关键词匹配，确保精确词不被稀释）
       ↓
  按 chunk id 合并，Faiss 优先在前
       ↓
  去重 → 截取 TopK=3 → 传给 LLM
```

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

```
Frontend: crypto.randomUUID() 生成
  → Header: X-Trace-Id
Java TraceIdFilter: 读取 → MDC + request.setAttribute + 响应头
  → Header: X-Trace-Id（透传给 Python）
Python middleware: 读取 → request.state.trace_id + 响应头
  → JSON: { "traceId": "..." }
Frontend: 展示 traceId 标签
```

任何一环缺失 traceId 都会自动生成兜底。

## 异常兜底设计

| 场景 | 处理 |
|------|------|
| Python 服务不可用 | Java 返回 `success=false`，traceId 仍然存在 |
| LLM 调用失败 | Python rag_service 返回 `success=false`，日志记录异常 |
| 知识库无检索结果 | Prompt 兜底："当前知识库暂无相关信息，不要编造" |
| 安全问题输入 | Safety Guard 拦截，返回 `safe=false, route=refuse` |
| Agent 异常 | Python endpoint catch Exception，返回 `success=false` |

## Python 模块一览

```
agent-python/app/
├── core/          # config.py — 环境变量、路径、常量
├── retrieval/     # faiss_retriever, keyword_retriever, hybrid_retriever
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
