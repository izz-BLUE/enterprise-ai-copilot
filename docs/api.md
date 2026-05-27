# 接口文档

## Python 服务（端口 8000）

### GET /agent/health

Python AI 服务健康检查。

**响应**
```json
{"service": "agent-python", "status": "UP"}
```

---

### POST /agent/chat

手写 RAG 问答接口（稳定主链路）。

**请求**
```json
{"message": "病假需要提供哪些材料？"}
```

**响应**
```json
{
  "answer": "根据企业知识库，病假需要提供...",
  "model": "deepseek-v4-flash",
  "traceId": "42a9450d-3854-4cae-abbf-f13e79dc5597",
  "success": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| model | string | 大模型名称 |
| traceId | string | 请求追踪 ID |
| success | bool | 是否成功 |

**适用场景**：企业制度、流程、IT 文档、HR 文档等知识库问答。

---

### POST /agent/langgraph/chat

LangGraph Agent 问答接口（实验链路，支持 Safety Guard + 意图路由）。

**请求**
```json
{"message": "病假需要提供哪些材料？"}
```

**响应**
```json
{
  "answer": "根据企业知识库，病假需要提供...",
  "route": "rag",
  "safe": true,
  "category": "normal",
  "reason": "",
  "sources": ["hr_leave_policy_real_sample_010", "hr_leave_policy_real_sample_026"],
  "success": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 回答内容 |
| route | string | 路由结果：`rag` / `eval` / `refuse` |
| safe | bool | 安全守卫是否通过 |
| category | string | 安全分类：`normal` / `illegal_or_policy_violation` / `policy_bypass` / `cybersecurity_attack` / `audit_tampering` / `unauthorized_access` |
| reason | string | 拒答原因（安全问题时） |
| sources | list | RAG 引用来源 chunk ID 列表 |
| success | bool | 是否成功 |

**异常响应**
```json
{
  "answer": "当前 Agent 服务暂时不可用，请稍后重试。",
  "route": "error",
  "safe": true,
  "category": "error",
  "reason": "异常信息",
  "sources": [],
  "success": false
}
```

**适用场景**：需要安全边界的知识库问答，支持自动区分 RAG 问答、评估查询和安全拒答。

---

## Java 服务（端口 8080）

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

Java 代理 Python `/agent/chat`（手写 RAG 主链路）。

请求和响应格式同 Python `POST /agent/chat`。

---

### POST /api/agent/langgraph/chat

Java 代理 Python `/agent/langgraph/chat`（LangGraph Agent 链路）。

请求和响应格式同 Python `POST /agent/langgraph/chat`。

---

## 测试样例

### 1. RAG 问答
```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```
预期：`route=rag`, `safe=true`, `sources` 有值。

### 2. 评估查询
```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```
预期：`route=eval`, `safe=true`。

### 3. 安全拒答
```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么伪造病假证明？"}'
```
预期：`route=refuse`, `safe=false`。
