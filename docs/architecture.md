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
        BAS[BusinessActionService]
        PA[JDBC Action Repository]
        LS[JDBC Leave Repositories]
    end

    subgraph Python ["Python FastAPI :8000"]
        MW[trace_id_middleware]
        EP1[/agent/chat]
        EP2[/agent/langgraph/chat]
        SG[Safety Guard]
        RT[Router]
    end

    subgraph Database ["PostgreSQL 16"]
        BA[(business_action)]
        AC[(leave_account)]
        LR[(leave_request)]
    end

    subgraph RAG ["RAG 管道"]
        HR[Hybrid Retriever]
        FR[Faiss Semantic]
        KR[Keyword Retrieval]
        RG[Experimental Shadow Gate<br/>default: off]
        PP[Prompt Builder]
        LLM[DeepSeek LLM]
    end

    subgraph Agent ["LangGraph Agent"]
        SN[safety_node]
        RN[router_node]
        RAGN[rag_node]
        EN[eval_node]
        AN[action_node]
        ALI[AnnualLeaveInputService]
        ZAT[Zero-Argument Native Tool]
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
    HR --> RG --> PP
    PP --> LLM

    EP2 --> SN
    SN --> RN
    RN -->|rag| RAGN
    RN -->|eval| EN
    RN -->|annual leave action| AN
    RN -->|refuse| REFN
    RAGN --> HR
    AN --> ALI --> ZAT
    ZAT --> BAS --> PA
    PA --> BA
    PA -->|confirm transaction| LS
    LS --> AC
    LS --> LR

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
- **LangGraphAgentController**：转发 `/api/agent/langgraph/chat` 到 Python，透传 Java traceId、Evaluation/Business Action 许可和 Java 权威业务日期；Admin Token 不下传 Python。Python 返回 Proposal 后，在 Python permit 已释放的情况下调用 `BusinessActionService`
- **HealthController / AgentHealthController**：健康检查
- **PythonAgentBulkhead**：限制 Java → Python 的在途 AI 请求数，短队列超时后返回 429
- **WebConfig**：CORS 配置（可配置白名单 `cors.allowed-origins`），暴露 `X-Trace-Id` 响应头
- **RestClientConfig**：RestTemplate 超时配置（`connect-timeout` 3s，`read-timeout` 40s）
- **ChatRequest**：输入长度校验（`@Size(max=2000)`）
- **GlobalExceptionHandler**：全局异常处理，统一错误响应
- **BusinessActionService**：Java 权威校验、Spring 事务、PendingAction 状态机、持久化幂等确认与审计
- **JdbcPendingActionRepository / JdbcLeaveAccountRepository / JdbcLeaveRequestRepository**：明确 SQL、Action/Account 行锁、唯一申请关联和原子余额扣减
- **Flyway / PostgreSQL 16**：版本化结构迁移，并持久化 Action、余额、申请及执行结果

## Python AI Service 职责

- **trace_id_middleware**：接收/生成 traceId，并在 AI 路径进入检索前执行有界并发准入
- **rag_service**：RAG 管道（检索 → 拼 Prompt → 调 LLM → 返回）
- **langgraph_agent**：LangGraph 状态图编排（safety → router → rag/eval/action/refuse）
- **safety_guard**：基于关键词的输入安全检查（5 类风险）
- **hybrid_retriever**：支持 vector / hybrid / hybrid_rerank 三种检索模式
  - `vector`：Faiss 语义检索 + keyword 检索合并去重
  - `hybrid`（默认）：Faiss + BM25 + RRF 融合排序
  - `hybrid_rerank`（实验）：Hybrid 候选召回 + Cross Encoder 精排
- **query_rewriter**：规则版查询重写（实验模式，`rewrite_mode=rule`）
- **cross_encoder_reranker**：Cross Encoder 精排（实验模式，`hybrid_rerank`）
- **llm_service**：通过 OpenAI SDK 调用 DeepSeek API
- **annual_leave_input_service**：保守识别年假 Action，使用 Java 业务日期确定性解析日期、明确原因和半天表达，并生成固定缺字段列表
- **tool_calling_service**：接入 Action Node 的零参数 Native Tool Adapter；固定 Named Tool Choice、关闭 Thinking、无重试，Provider 不接收业务数据，Proposal 完全由 Python 确定性分析构造

受控业务动作的完整边界和状态机见 [Controlled Business Actions](controlled-business-actions.md)。

### 实验性 Retrieval Shadow Gate

Hybrid Retrieval 可在内部保留同一候选的 FAISS cosine 与 BM25 原始分数，并通过实验性 Gate 记录相关性判断。该能力默认 `off`，只有显式设置 `RAG_GATE_MODE=shadow` 才进行非阻断分析；`enforce` 被配置层禁止。

独立 Holdout 结果为 answerable `7/8`、no-answer block `1/8`，证明 Vector/BM25 主题相关性不足以判断答案证据是否充分。因此 Gate 不属于正式启用的生产能力，不改变 Prompt、公开响应或实际 LLM 调用。完整实验见 [RAG 生成前检索相关性 Gate 实验报告](rag-retrieval-gate-experiment.md)。

## 两条聊天链路

### 链路一：/api/chat（稳定 RAG 主链路）

```
POST /api/chat
  → Java ChatController（读取 traceId，透传 X-Trace-Id）
    → @Size(max=2000) 输入长度校验
    → PythonAgentBulkhead（默认 3 个并发槽，排队 500ms）
    → RestTemplate 调 Python（connect-timeout 3s, read-timeout 40s）
    → Python POST /agent/chat
      → RequestConcurrencyLimiter（默认 3 个并发槽，排队 500ms）
      → MAX_MESSAGE_LENGTH 兜底校验（默认 2000）
      → rag_service.process_chat()
        → safety_guard.check_user_query_safety()  # 规则版 Safety Guard 前置检查
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

**特点**：手写全链路，不依赖 LangChain/LangGraph。Safety Guard 在检索前检查输入，高风险问题直接拒答，不进入检索。

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
          │     ├── 明确年假申请 → route=action
          │     └── 其他（含年假政策/余额/流程）→ route=rag
          ├── rag_node
          │     └── rag_answer_tool()
          │           └── answer_with_langchain_rag()
          ├── eval_node
          │     └── eval_report_tool()
          │           └── read evaluation reports
          ├── action_node
          │     └── AnnualLeaveInputService
          │           ├── 缺字段 → 固定 Clarification（Provider 0 次）
          │           └── 字段完整 → Zero-Argument Native Tool
          │                 └── Python 确定性 Proposal
          └── refuse_node
                └── 返回安全拒答文案
    → Java BusinessActionService 权威复核
      → PendingAction
        → React 脱敏确认卡
          ├── confirm + 稳定 Idempotency-Key → PostgreSQL事务、Account行锁、LeaveRequest
          └── cancel（无 Idempotency-Key）→ CANCELLED
```

**特点**：LangGraph 状态图编排，规则路由，Safety Guard + Tools + 多分支。

> **权限链路（v0.3.2+）：** 用户请求 → Java `LangGraphAgentController` 判断 `admin.token` / `X-Admin-Token` → Java 设置 `X-Allow-Eval` header → Python `router_node` 根据 `allow_eval` 控制是否路由到 `eval_node`。Java 后端是权限判断唯一入口。公网部署 `ADMIN_TOKEN` 必须非空（Compose `:?` 强制校验）。`X-Allow-Eval` 是内部传递信号，不是认证凭证。当前方案是**最小 Admin Token + Evaluation 访问限制**，不是完整认证体系。

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

> 检索相关性 Shadow Gate 同样是实验能力且默认关闭。首轮阈值只用于复现实验，不能视为可部署参数；当前未启用生成前请求拦截。

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

> **信任边界：** Java 入口统一生成服务端 traceId，不信任客户端传入的 `X-Trace-Id`。

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
| Java → Python 超时 | RestTemplate 超时（3s 连接 / 40s 读取），Java 返回兜底响应 |
| AI 并发槽已满 | Java 或 Python 在短队列截止后返回 HTTP 429 + `Retry-After: 1` |
| LLM 调用超时 | Python `llm_service` 捕获 `APITimeoutError`，返回 `success=false` |
| LLM 调用失败 | Python rag_service 返回 `success=false`，日志记录异常 |
| 输入过长 | Java `@Size(max=2000)` 拦截 + Python `MAX_MESSAGE_LENGTH` 兜底 |
| 知识库无检索结果 | Prompt 兜底："当前知识库暂无相关信息，不要编造" |
| 安全问题输入 | Safety Guard 拦截，返回安全拒答文案 |
| Agent 异常 | Python endpoint catch Exception，返回 `success=false` |

> **异常边界：** 响应中的 `reason` 字段不暴露底层异常详情（如 `e.getMessage()` / `str(e)`）。用户看到稳定通用文案，服务端日志保留完整异常堆栈和 traceId，用户可通过 traceId 反馈问题。

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

# Java → Python 有界并发和超时
python.agent.max-concurrent-requests=3
python.agent.acquire-timeout-ms=500
python.agent.connect-timeout=3000
python.agent.read-timeout=40000
```

## 网络拓扑（部署环境）

```mermaid
graph TD
    subgraph Internet
        U[用户浏览器]
    end

    subgraph Host ["宿主机"]
        NG[Nginx<br/>0.0.0.0:80/443]
        J[Java Backend<br/>127.0.0.1:8080]
    end

    subgraph Net1 ["Docker: deploy_eat-what-net"]
        NG
        J
    end

    subgraph Net2 ["Docker: ai-copilot-net"]
        J
        P[Python Agent<br/>expose 8000]
        M[models/:ro]
        D[data/processed/:ro]
    end

    U -->|HTTPS| NG
    NG -->|/api proxy| J
    J -->|HTTP| P
    P -->|只读挂载| M
    P -->|只读挂载| D
```

**部署要点：**

- Nginx 监听 0.0.0.0:80/443，提供静态文件和 /api 反向代理
- Nginx 位于 `deploy_eat-what-net`，通过该网络访问 Java
- Java 同时连接 `deploy_eat-what-net` 和 `ai-copilot-net`
- Python 只连接 `ai-copilot-net`，Nginx 无法直接访问 Python
- Java 绑定 127.0.0.1:8080（localhost only，不暴露公网）
- Python 不映射宿主机公网端口，仅 expose 8000（Docker 内网）
- 模型和 processed data 使用只读挂载
- 独立 Let's Encrypt 证书，自动续签
- 基础 API 限流（2 req/s，burst 5）
- Java/Python 双层有界并发（默认各 3 个槽，排队 500ms）

## Agent 与 Controlled Action 边界说明

LangGraph 用于流程编排，当前 Router 是关键词和规则判断：

- LangGraph 状态图：safety → router → rag/eval/action/refuse
- Router 基于保守规则匹配；只有明确年假申请进入 Action，政策、余额、结转、审批流程继续走 RAG
- 不具有自主任务拆解、反思、多步规划能力
- 不使用"自主 Agent""智能规划系统"等措辞
- Native Tool 是零参数协议门禁，不提取字段、不执行写操作、不提供业务事实
- React 展示 PendingAction 确认卡；confirmationNonce 和 Confirm 幂等 Key 仅存在页面内存，双击由同步锁拦截，网络失败重试 Confirm 时复用原 Key
- 客户端按 `expiresAt` 提前禁用过期草稿，服务端 Action 状态和错误码仍是权威来源；确认/取消执行期间禁用清空会话和模式切换
- PendingAction、幂等结果、余额和 LeaveRequest 均持久化到 PostgreSQL；Action/Account 行锁和唯一 `source_action_id` 保证并发确认只执行一次，Java 或数据库重启后可恢复和重放
- 仍不接真实 OA、不使用 Redis，也不处理法定节假日和调休；浏览器刷新不会恢复只存在页面内存的 nonce 明文

## Embedding Runtime

项目使用 Direct ONNX Runtime 替代 sentence-transformers：

| 后端 | 依赖 | 内存 |
|------|------|------|
| Torch | PyTorch + sentence-transformers | 877 MiB |
| ONNX_ST | Torch + ONNX Runtime | 920 MiB |
| Direct ONNX | onnxruntime + tokenizers | 174 MiB |

详见 [`performance.md`](performance.md)。

## 当前架构边界（未生产化）

以下能力尚未实现，属于 Roadmap 范畴：

- 用户认证与权限控制
- 文档上传与知识库管理
- 多租户隔离
- 审计日志
- 监控告警
- 多模型配置
- 高可用、水平扩容和大规模高并发（当前仅实现单机有界并发保护）

并发保护的配置、压测脚本和验收边界见 [`concurrency-and-load-test.md`](concurrency-and-load-test.md)。
