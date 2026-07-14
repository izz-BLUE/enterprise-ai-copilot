# 架构讲解稿

> 5 分钟架构讲解。可配合白板或架构图使用。

---

## 总体架构

```
用户
  → React Frontend (:5173)
    → Vite proxy 只转发 /api 到 Java
  → Java Spring Boot (:8080)
    → 统一入口、权限判断、traceId 管理、异常兜底、CORS
    → Bulkhead 准入（3 个槽，排队 500ms）
    → RestTemplate 调 Python（connect-timeout 3s, read-timeout 40s）
  → Python FastAPI (:8000)
    → RAG 检索、Prompt 构造、LLM 调用、Agent 编排
    → 内部能力层，不直接暴露给前端
```

**核心设计原则：** Java 是唯一的对外入口，Python 是内部能力层。前端只调 Java，不调 Python。

---

## Java Backend 职责

| 组件 | 职责 |
|------|------|
| `TraceIdFilter` | 统一生成 UUID 格式 traceId，存入 MDC 和 request attribute，设置响应头 |
| `ChatController` | 转发 `/api/chat` 到 Python `/agent/chat`，透传 traceId |
| `LangGraphAgentController` | 转发 `/api/agent/langgraph/chat`，校验 `admin.token`，设置 `X-Allow-Eval` |
| `PythonAgentBulkhead` | 限制 Java → Python 在途 AI 请求，过载返回 429 |
| `WebConfig` | CORS 白名单（`cors.allowed-origins`） |
| `RestClientConfig` | RestTemplate 超时配置 |
| `ChatRequest` | `@Size(max=2000)` 输入校验 |
| `GlobalExceptionHandler` | 全局异常处理 |

**话术：**
> Java 做的是"控制面" — 权限、安全、超时、异常兜底。不做 AI 逻辑，只做转发和保护。

---

## Python Agent 职责

| 组件 | 职责 |
|------|------|
| `trace_id_middleware` | 接收/生成 traceId，设置响应头 |
| `rag_service` | RAG 管道（Safety Guard → 检索 → Prompt → LLM） |
| `langgraph_agent` | LangGraph 状态图编排（safety → router → rag/eval/refuse） |
| `safety_guard` | 5 类风险关键词匹配 |
| `hybrid_retriever` | Faiss + BM25 + RRF 融合检索 |
| `llm_service` | OpenAI SDK 调用 DeepSeek API |
| `eval_report_tool` | 读取本地 JSON 评估报告 |

**话术：**
> Python 做的是"数据面" — 检索、生成、评估。所有 AI 逻辑都在 Python，Java 不碰。

---

## RAG 主链路

```
POST /api/chat
  → Java ChatController
    → @Size(max=2000) 输入校验
    → RestTemplate 调 Python（3s/30s 超时）
  → Python /agent/chat
    → MAX_MESSAGE_LENGTH 兜底校验
    → Safety Guard 前置检查
    → Hybrid Retrieval
      ├── Faiss 语义检索（BGE embedding 余弦相似度）
      └── BM25 关键词检索（字符级 n-gram，无 jieba 依赖）
    → RRF 融合排序 → TopK=3
    → build_rag_prompt()
    → LLM 调用（LLM_TIMEOUT 30s）
    → ChatResponse
```

**话术：**
> RAG 主链路是手写的，不依赖 LangChain。检索用 Faiss 做语义召回、BM25 做关键词精确匹配，然后用 RRF（Reciprocal Rank Fusion）融合排序。RRF 的好处是不需要分数归一化，直接按排名融合。TopK=3 的 chunk 拄给 LLM 生成回答。

---

## LangGraph Agent 链路

```
POST /api/agent/langgraph/chat
  → Java LangGraphAgentController
    → 校验 admin.token / X-Admin-Token
    → 设置 X-Allow-Eval header
    → RestTemplate 调 Python
  → Python /agent/langgraph/chat
    → StateGraph.invoke()
      ├── safety_node → check_user_query_safety()
      │     ├── unsafe → route=refuse
      │     └── safe → 继续
      ├── router_node
      │     ├── allow_eval + eval 关键词 → route=eval
      │     └── 其他 → route=rag
      ├── rag_node → rag_answer_tool()
      ├── eval_node → eval_report_tool()
      └── refuse_node → 安全拒答文案
```

**话术：**
> Agent 链路用 LangGraph 做状态图编排。safety_node 做输入安全检查，router_node 做意图路由 — 匹配"评估""通过率"等关键词走 eval，其他走 rag。refuse_node 处理安全拒答和权限拒绝。

---

## Evaluation 链路

```
用户："当前RAG评估通过率是多少？"
  → Java 判断 admin.token
    → admin.token 为空（Demo 模式）→ X-Allow-Eval: true
    → admin.token 非空 + X-Admin-Token 匹配 → X-Allow-Eval: true
    → admin.token 非空 + X-Admin-Token 不匹配 → X-Allow-Eval: false
  → Python router_node
    → allow_eval=true + eval 关键词 → eval_node
    → allow_eval=false + eval 关键词 → refuse_node (access_control)
  → eval_node → eval_report_tool
    → 读取 data/eval/reports/ 下 JSON 文件
    → 返回检索评估和生成评估摘要
```

**话术：**
> Evaluation 定位为管理员诊断能力。Java 是权限判断入口，通过 `X-Allow-Eval` header 告知 Python 是否允许 eval 路由。`X-Allow-Eval` 不是认证凭证，只是内部传递信号。本地脚本 `scripts/eval/run_rag_eval.py` 不经过 HTTP，不受此限制。

---

## 安全边界

| 边界 | 说明 |
|------|------|
| Safety Guard | 5 类风险关键词匹配，覆盖 RAG + Agent 两条链路 |
| 输入校验 | Java `@Size(max=2000)` + Python `MAX_MESSAGE_LENGTH` |
| 超时控制 | Java RestTemplate 3s/30s + Python LLM 30s |
| CORS | 从 `*` 收敛为可配置白名单 |
| traceId | 服务端统一生成，不信任客户端 |
| 异常收敛 | `reason` 字段不暴露 `e.getMessage()` / `str(e)` |
| Admin Token | 保护 Evaluation，`admin.token` 为空时为 Demo 模式 |

**话术：**
> 安全是分层的。Safety Guard 做输入层防护，输入校验做长度限制，超时控制做资源保护，CORS 做跨域限制，traceId 做链路追踪，异常收敛做信息保护，Admin Token 做权限边界。

---

## 权限边界

| 角色 | 能力 | 认证方式 |
|------|------|----------|
| 普通用户 | RAG 问答 | 无 |
| 管理员 | RAG + Evaluation | `X-Admin-Token` 匹配 `admin.token` |

**关键约束：**
- 前端 role 不可信，权限判断在 Java 后端
- `X-Allow-Eval` 不是认证凭证，是 Java → Python 内部信号
- Python 服务只在 Docker 内网暴露，外部请求必须经过 Nginx 和 Java
- 生产环境必须配置非空 `admin.token`；空值只适用于本地开发

**话术：**
> 权限方案是最小 Admin Token，不是完整用户体系。优点是实现简单、不引入复杂登录；缺点是共享 Token、无 per-user 身份。如果要上线，需要替换为 JWT + 用户体系。

---

## traceId 链路

```
Frontend: 发送请求（X-Trace-Id 可选，不被信任）
  ↓
Java TraceIdFilter: 忽略客户端值，统一生成 UUID
  → MDC + request.setAttribute + 响应头
  ↓
Java → Python: X-Trace-Id（服务端生成，透传）
  ↓
Python middleware: 读取 → request.state.trace_id + 响应头
  → JSON: { "traceId": "..." }
Frontend: 展示 traceId 标签
```

**话术：**
> traceId 由 Java 入口统一生成，格式是 UUID v4。客户端传入的非法格式（含控制字符、超长、非 UUID）会被丢弃重新生成。两端日志都带 traceId，用户反馈问题时只需要提供 traceId，服务端通过日志就能定位全链路。

---

## timeout / fallback 链路

| 场景 | 处理 |
|------|------|
| Java → Python 连接超时 | RestTemplate connect-timeout 3s，Java 返回兜底响应 |
| Java → Python 读取超时 | RestTemplate read-timeout 40s，Java 返回兜底响应 |
| Java/Python 并发槽满 | 最多排队 500ms，然后返回 429 + `Retry-After` |
| Python LLM 调用超时 | LLM_TIMEOUT 30s，捕获 APITimeoutError |
| Python LLM 连接失败 | 捕获 APIConnectionError |
| Python 服务不可用 | Java catch Exception，返回 `success=false` |
| 知识库无检索结果 | Prompt 兜底："当前知识库暂无相关信息，不要编造" |

**话术：**
> 保护是分层的。Java 和 Python 各自限制 3 个在途 AI 请求，最多排队 500ms；获得槽位后，Python 的 LLM 超时是 30 秒，Java 读取超时 40 秒，Nginx 上游超时 45 秒。这样过载先快速 429，正常慢请求再受递增超时预算约束。

---

## 为什么不把所有能力都放在 Java？

**回答：**

1. Python 的 AI 生态（LangChain、LangGraph、FAISS、sentence-transformers）比 Java 成熟
2. Java 擅长控制面（权限、超时、异常兜底），Python 擅长数据面（检索、生成、评估）
3. 职责分离后，AI 逻辑迭代不影响业务网关
4. 如果未来换 LLM Provider 或 Embedding 模型，只改 Python

---

## 为什么 Python 服务不能直接暴露公网？

**回答：**

1. Python 无认证/授权机制，任何人可直接调用
2. 绕过 Java 层的安全检查（Safety Guard、输入校验、Admin Token）
3. 绕过 Java 层的超时控制
4. 攻击者可伪造 `X-Allow-Eval: true` 直接访问 Evaluation
5. 当前部署通过 Docker 网络隔离 Python，公网只暴露 Nginx

---

## 公网部署如何控制边界？

**回答：**

1. Nginx 负责 HTTPS、静态资源、反向代理和入口限流
2. Java 只绑定宿主机 localhost，Python 不映射宿主机端口
3. 管理员 Evaluation 需要非空 Token，普通问答保持公开演示
4. Java/Python 都设置并发槽和超时，过载显式返回 429
5. 当前仍缺少正式用户认证、高可用和完整监控，因此不承诺生产 SLA
