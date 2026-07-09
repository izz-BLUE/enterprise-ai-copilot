# 02 - API 契约（API Contract）

> **修改任何接口前必须先读此文档。Java ↔ Python 契约手动对齐，改一端必须同步另一端。**

## 对外接口（Frontend → Java）

### POST /api/chat

稳定 RAG 主链路。

**Request:**
```json
{
  "message": "病假需要提供哪些材料？"
}
```

**Response:**
```json
{
  "answer": "根据知识库...",
  "model": "deepseek-chat",
  "traceId": "xxx",
  "success": true
}
```

> 注：`/api/chat` 响应不包含 `sources` 字段。RAG 引用来源仅在 Agent 链路返回。

**Owner:** 全栈开发（Java）+ AI/RAG 工程师（Python）

---

### POST /api/agent/langgraph/chat

实验 Agent 链路。

**Request:**
```json
{
  "message": "当前RAG评估通过率是多少？"
}
```

**Response（RAG 问答）:**
```json
{
  "answer": "根据知识库...",
  "route": "rag",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": ["hr_leave_policy_real_sample_010"],
  "success": true,
  "traceId": "xxx"
}
```

**Response（安全拒答）:**
```json
{
  "answer": "抱歉，我不能协助处理该请求。",
  "route": "refuse",
  "safe": false,
  "category": "illegal_or_policy_violation",
  "reason": "检测到高风险关键词「伪造」",
  "sources": [],
  "success": true,
  "traceId": "xxx"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| answer | string | 回答内容 |
| route | string | 路由结果：`rag` / `eval` / `refuse` / `error` |
| safe | bool | 安全守卫是否通过 |
| category | string | 安全分类 |
| reason | string | 拒答原因（安全问题时） |
| sources | list | RAG 引用来源 chunk ID 列表 |
| success | bool | 是否成功 |
| traceId | string | 请求追踪 ID |

**Owner:** AI/RAG 工程师

---

### GET /api/health

Java 服务健康检查。

**Response:** `{"service": "backend-java", "status": "UP"}`

---

### GET /api/agent/health

Python 服务健康检查（Java 代理）。

**Response:** `{"service": "agent-python", "status": "UP"}`

---

## 内部接口（Java → Python）

### POST /agent/chat

**Request:**
```json
{
  "message": "病假需要提供哪些材料？"
}
```

> traceId 通过 `X-Trace-Id` 请求头透传，不在 body 中。

**Response:**
```json
{
  "answer": "根据知识库...",
  "model": "deepseek-chat",
  "traceId": "xxx",
  "success": true
}
```

---

### POST /agent/langgraph/chat

**Request:** 同 `/agent/chat`（`{"message": "..."}` + `X-Trace-Id` header）

**Response:** 同对外接口 `/api/agent/langgraph/chat` 格式。

---

### GET /agent/health

**Response:** `{"service": "agent-python", "status": "UP"}`

---

## DTO 契约风险

当前 Java ↔ Python 的 DTO 手动对齐，**无共享 schema、无编译时校验**。

| 风险 | 说明 |
|---|---|
| 字段名不一致 | 一端改名另一端不会报错 |
| 类型不匹配 | 一端改类型另一端静默失败 |
| 新增字段未同步 | 前端可能读到 undefined |

**建议后续：** 引入 OpenAPI spec 或共享 DTO 定义。

## traceId 透传链路

```
Frontend: crypto.randomUUID() → X-Trace-Id header
Java: TraceIdFilter → MDC + request.setAttribute + 透传 header
Python: trace_id_middleware → request.state.trace_id + 透传 header
Response: header + body 都包含 traceId
```

## Java Fallback

Python 不可用时，Java 返回：
```json
{
  "answer": "当前 AI 服务暂时不可用，请稍后重试。",
  "success": false,
  "traceId": "..."
}
```

## 修改接口的流程

1. 确认修改范围（哪一端、哪些字段）
2. 更新 `docs/api.md`
3. 同步 Java DTO / Python Schema
4. 同步 Frontend 调用代码
5. 运行验证（curl 测试 + eval）
6. 更新本文档
