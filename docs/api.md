# 接口文档

## 本地服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| Java Spring Boot | `http://localhost:8080` | 业务网关，统一入口 |
| Python FastAPI | `http://localhost:8000` | AI 引擎，RAG + Agent |
| React Frontend | `http://localhost:5173` | 前端演示页面 |

## Java Backend API（端口 8080）

### GET /api/health

Java 服务健康检查。

**响应**
```json
{"service": "backend-java", "status": "UP"}
```

---

### GET /api/agent/health

Java 代理 Python 健康检查。

**响应**（转发 Python 原始响应）
```json
{"service": "agent-python", "status": "UP"}
```

---

### POST /api/chat

**稳定 RAG 主链路。** Java 代理 Python `/agent/chat`。

> **安全说明（Phase 3 Batch 1）：** RAG 主链路现在会先经过 Safety Guard 前置检查。高风险问题（如伪造、违法、攻击等）会被直接拦截并返回安全拒答文案，不会进入检索和 LLM 调用。安全拒答时 `success=true`，表示系统成功处理并拒绝了高风险请求。当前 Safety Guard 是规则版基础防护（5 类风险关键词匹配），不是完整安全系统。

**请求**
```json
{"message": "病假需要提供哪些材料？"}
```

**响应**
```json
{
  "answer": "根据企业知识库，病假需要提供：\n1. 病历本复印件\n2. 缴费清单\n3. 病假证明",
  "model": "deepseek-v4-flash",
  "traceId": "551245e6-a04b-442d-adef-99387f93cd23",
  "success": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| model | string | 大模型名称 |
| traceId | string | 请求追踪 ID（全链路一致） |
| success | bool | 是否成功 |

> 注：当前版本 `/api/chat` 响应中不包含 `sources` 字段。RAG 引用来源仅在 Agent 链路（`/api/agent/langgraph/chat`）的 `sources` 字段返回。

**异常响应**（Python 不可用时）
```json
{
  "answer": "当前 AI 服务暂时不可用，请稍后重试。",
  "model": "unknown",
  "traceId": "c6d65330-7aca-4c1e-895e-c5f6b6e24a94",
  "success": false
}
```

**超长输入响应**（message 超过 2000 字符）
```json
{
  "answer": "输入内容过长，请精简后重试。",
  "model": "deepseek-chat",
  "traceId": "xxx",
  "success": false
}
```

> **Phase 3 Batch 2 说明：**
> - Java 侧通过 `@Size(max=2000)` 校验输入长度，Python 侧通过 `MAX_MESSAGE_LENGTH` 兜底校验。
> - Java 调 Python 配置了连接超时（3s）和读取超时（30s）。
> - Python LLM 调用配置了超时（默认 30s），超时或连接失败会返回错误响应。
> - CORS 已从 `*` 收敛为可配置白名单（`cors.allowed-origins`）。
> - 以上不改变正常 RAG 响应结构。

**适用场景**：企业制度、流程、IT 文档、HR 文档等知识库问答。

---

### POST /api/agent/langgraph/chat

**实验性 Agent 链路。** Java 代理 Python `/agent/langgraph/chat`，支持 Safety Guard + 意图路由 + Tool Calling。

**请求**
```json
{"message": "病假需要提供哪些材料？"}
```

**响应**（RAG 问答）
```json
{
  "answer": "根据企业知识库，病假需要提供...",
  "route": "rag",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": ["hr_leave_policy_real_sample_010", "hr_leave_policy_real_sample_026"],
  "success": true,
  "traceId": "387af8a3-5357-4a0c-8e48-77505524a8f3"
}
```

**响应**（评估查询）
```json
{
  "answer": "检索评估: 8/8 通过, final_pass_rate=1.0；生成评估: 8/8 通过...",
  "route": "eval",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": [],
  "success": true,
  "traceId": "..."
}
```

**响应**（安全拒答）
```json
{
  "answer": "抱歉，我不能协助处理该请求。",
  "route": "refuse",
  "safe": false,
  "category": "illegal_or_policy_violation",
  "reason": "检测到高风险关键词「伪造」，属于「违法违规 / 伪造材料」类别。",
  "sources": [],
  "success": true,
  "traceId": "..."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| route | string | 路由结果：`rag` / `eval` / `refuse` / `error` |
| safe | bool | 安全守卫是否通过 |
| category | string | 安全分类：`normal` / `illegal_or_policy_violation` / `policy_bypass` / `cybersecurity_attack` / `audit_tampering` / `unauthorized_access` / `error` |
| reason | string | 拒答原因（安全问题时） |
| sources | list | RAG 引用来源 chunk ID 列表 |
| success | bool | 是否成功 |
| traceId | string | 请求追踪 ID |

**适用场景**：需要安全边界的知识库问答，支持自动区分 RAG 问答、评估查询和安全拒答。

---

## Python AI Service API（端口 8000）

### GET /agent/health

Python AI 服务健康检查。

**响应**
```json
{"service": "agent-python", "status": "UP"}
```

---

### POST /agent/chat

手写 RAG 问答接口（稳定主链路）。

请求和响应格式同 Java `POST /api/chat`。

---

### POST /agent/langgraph/chat

LangGraph Agent 问答接口（实验链路）。

请求和响应格式同 Java `POST /api/agent/langgraph/chat`。

---

## 测试样例

### 1. RAG 问答

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期：`success=true`，answer 包含病假材料清单。

### 2. Agent RAG 问答

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期：`route=rag`, `safe=true`, `sources` 有值。

### 3. 评估查询

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```

预期：`route=eval`, `safe=true`，answer 包含评估指标。

### 4. 安全拒答

```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么伪造病假证明？"}'
```

预期：`route=refuse`, `safe=false`。

### 5. Python 停服降级

```bash
# 先停止 Python 服务，再调用 Java
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"测试"}'
```

预期：`success=false`，answer 为"当前 AI 服务暂时不可用"，traceId 仍然存在。

---

## traceId 全链路

所有接口支持 `X-Trace-Id` 请求头透传：

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -H "X-Trace-Id: my-custom-trace-123" \
  -d '{"message":"上班时间是什么？"}'
```

- 如果请求头带 `X-Trace-Id`，Java 和 Python 沿用
- 如果不带，Java 自动生成 UUID
- 响应头和响应体都包含 `X-Trace-Id` / `traceId`
- Java 日志和 Python 日志都带 traceId，便于全链路排查

---

## 评估用例字段说明

评估用例文件：`data/eval/rag_eval_cases.json`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 用例唯一标识，如 `leave_001` |
| question | string | 用户问题 |
| expected_sources | list[string] | 预期命中的文档来源文件名 |
| expected_keywords | list[string] | 检索结果应包含的关键词（Retrieval Eval 用） |
| expected_answer_keywords | list[string] | LLM 回答应包含的关键词（Generation Eval 用） |
| expected_answer_keyword_groups | list[list[string]] | 同义词组，组内 OR、组间 AND（可选） |
| answerable | bool | `true` = 有答案，`false` = 无答案负样本 |

### keyword_groups 同义词组机制

`expected_answer_keyword_groups` 用于支持合理同义表达：

```json
{
  "expected_answer_keyword_groups": [
    ["直接主管", "上级主管", "直属主管", "主管领导", "直属上级", "直属领导", "上级领导", "部门领导"]
  ]
}
```

- 组内关键词：OR 关系（命中任意一个即可）
- 组间：AND 关系（每组都必须命中至少一个）
- 兼容 `expected_answer_keyword_groups` 和 `expected_answer_keywords` 共存

### failure_type 分类

Generation Eval 结果中的 `failure_type` 字段：

| 值 | 说明 |
|---|---|
| `passed` | 通过 |
| `keyword_too_strict` | 关键词过严，模型回答合理但未命中预期词 |
| `generation_incomplete` | 模型没答全，遗漏关键信息 |
| `llm_flaky` | LLM 输出波动，retry 后通过 |
| `no_answer_leakage` | 无答案场景模型泄漏了编造内容 |

### 检索模式参数

| 参数 | 可选值 | 默认 | 说明 |
|---|---|---|---|
| `retrieval_mode` | `vector` / `hybrid` / `hybrid_rerank` | `hybrid` | 检索模式 |
| `rewrite_mode` | `none` / `rule` | `none` | 查询重写模式 |

> `hybrid_rerank` 和 `rewrite_mode=rule` 是实验模式，不建议默认启用。
