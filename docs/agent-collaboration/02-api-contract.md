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
  "model": "deepseek",
  "traceId": "xxx",
  "success": true,
  "sources": [
    {"document": "leave_policy.md", "chunk_id": "..."}
  ]
}
```

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

**Response:**
```json
{
  "answer": "...",
  "model": "deepseek",
  "traceId": "xxx",
  "success": true
}
```

**Owner:** AI/RAG 工程师

---

### GET /api/health

Java 服务健康检查。

**Response:** `{"status": "ok"}`

---

### GET /api/agent/health

Python 服务健康检查（Java 代理）。

**Response:** `{"status": "ok", "agent_ready": true}`

---

## 内部接口（Java → Python）

### POST /agent/chat

**Request:**
```json
{
  "message": "...",
  "trace_id": "xxx"
}
```

**Response:**
```json
{
  "answer": "...",
  "model": "deepseek",
  "trace_id": "xxx",
  "success": true,
  "sources": [...]
}
```

---

### POST /agent/langgraph/chat

**Request/Response:** 同上格式。

---

### GET /agent/health

**Response:** `{"status": "ok", "agent_ready": true}`

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
