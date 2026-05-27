# 架构说明

## 总体架构

```
Client / Postman / 前端
        │
        ▼
  Java Spring Boot (8080)
        │
        │  HTTP JSON
        ▼
  Python FastAPI (8000)
        │
        ├── RAG 链路 ──────▶ DeepSeek + Faiss + Knowledge Base
        │
        └── Agent 链路 ────▶ LangGraph (Safety → Router → Tools)
```

## 项目模块

| 模块 | 目录 | 说明 |
|------|------|------|
| backend-java | `backend-java/` | Java Spring Boot 业务系统，提供对外 API 并代理 Python 接口 |
| agent-python | `agent-python/` | Python FastAPI AI 服务，包含 RAG、Agent、Tools、Safety Guard |
| knowledge-base | `data/hr/ bank/ it/` | 企业知识库 Markdown 文档 |
| evaluation | `data/eval/` | RAG 评估测试集、报告和 baseline |
| docs | `docs/` | 项目文档、架构说明、接口文档 |

## 两条聊天链路

### 链路一：/api/chat（稳定 RAG 主链路）

```
POST /api/chat
  → Java ChatController
    → Python POST /agent/chat
      → rag_service.process_chat()
        → hybrid_retriever.retrieve()
          ├── faiss_retriever (BGE embedding)
          └── keyword_retriever (jieba 分词)
        → Merge + Dedup + TopK
        → build_rag_prompt()
        → llm_service.call_llm()
          → DeepSeek V4
        → ChatResponse
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

## Agent 节点说明

| 节点 | 职责 | 调用 |
|------|------|------|
| safety_node | 检查输入是否包含违法违规、绕过制度、攻击系统、删除审计、越权访问等高风险内容 | `check_user_query_safety()` |
| router_node | 根据安全结果和问题关键词决定下一节点 | 内置关键词规则 |
| rag_node | 调用 RAG 链回答知识库问题 | `rag_answer_tool.invoke()` |
| eval_node | 查询当前 RAG 评估报告状态 | `eval_report_tool.invoke()` |
| refuse_node | 返回安全拒答文案 | 直接返回 |

## 两条链路的设计意图

`/api/chat` 是**稳定 RAG 主链路**，经过多轮 chunk 优化、prompt 优化和评估验证，适用于生产环境的知识库问答。

`/api/agent/langgraph/chat` 是**Agent 实验链路**，集成 Safety Guard、意图路由、Tool Calling，用于验证 Agent 架构的可行性和扩展性。

采用**并行接口**而非替换，便于灰度验证 Agent 能力，同时不影响原有 RAG 链路的稳定性。

## Python 模块一览

```
agent-python/app/
├── core/          # config.py — 环境变量、路径、常量
├── retrieval/     # faiss_retriever, keyword_retriever, hybrid_retriever
├── services/      # rag_service.py (生产), llm_service.py
├── prompts/       # system_prompt.py, build_rag_prompt()
├── schemas/       # ChatRequest, ChatResponse, AgentResponse
├── chains/        # langchain_rag_chain.py — LangChain RAG 封装
├── tools/         # rag_answer_tool, eval_report_tool — LangChain @tool
├── agents/        # langgraph_agent.py — LangGraph Agent 状态图
├── guards/        # safety_guard.py — 输入安全边界控制
└── main.py        # FastAPI 应用入口
```

## 配置说明

Java 调用 Python 服务的地址配置在 `backend-java/src/main/resources/application.properties`：

```properties
python.agent.base-url=http://localhost:8000
```

两个 Controller 均通过 `@Value("${python.agent.base-url}")` 读取该配置：
- `ChatController` 拼接 `/agent/chat`
- `LangGraphAgentController` 拼接 `/agent/langgraph/chat`
