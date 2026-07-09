# Phase 3 Batch 3：权限与访问控制设计方案

> 基于 A4 Security Review 报告、Phase 3 修复计划、当前代码分析。
> 设计目标：最小可解释方案，不引入完整登录系统，不影响本地 Demo 快速启动。

---

## 1. 现状分析

### 1.1 当前暴露面

| 接口 | 认证 | 授权 | 风险 |
|------|------|------|------|
| `POST /api/chat` | ❌ 无 | ❌ 无 | 普通问答，风险低 |
| `POST /api/agent/langgraph/chat` | ❌ 无 | ❌ 无 | 含 eval 路由，泄露内部质量数据 |
| `GET /api/health` | ❌ 无 | ❌ 无 | 仅返回 `{"status":"UP"}`，风险极低 |
| `GET /api/agent/health` | ❌ 无 | ❌ 无 | 转发 Python 健康状态，风险低 |
| Python `:8000` 直接访问 | ❌ 无 | ❌ 无 | 绕过 Java 层所有检查 |

### 1.2 Evaluation 暴露路径

**无独立 HTTP eval 端点。** Evaluation 通过以下路径暴露：

```
前端 → POST /api/agent/langgraph/chat
  → Java LangGraphAgentController（透传，无判断）
  → Python /agent/langgraph/chat
    → LangGraph router_node（关键词匹配：评估/通过率/pass_rate/...）
      → eval_node → eval_report_tool → 读取 JSON 报告文件
```

触发方式：任何用户发送包含 `评估`、`通过率`、`pass_rate`、`命中率`、`baseline`、`回归`、`flaky` 关键词的消息即可访问评估报告。

### 1.3 traceId 现状

```
Frontend: crypto.randomUUID() → Header X-Trace-Id
  ↓
Java TraceIdFilter: 读取 header → null/blank 则生成 UUID
  → MDC + request.setAttribute("traceId") + 响应头
  ↓
Java Controller: httpRequest.getAttribute("traceId")
  → Header X-Trace-Id 透传给 Python
  ↓
Python trace_id_middleware: 读取 header → 无则生成 UUID
  → request.state.trace_id + 响应头
```

**问题：** 客户端可传入任意字符串作为 traceId（含控制字符、超长文本、伪造格式），Java 和 Python 均直接信任。

### 1.4 异常信息泄露点

| 位置 | 泄露内容 |
|------|----------|
| `LangGraphAgentController.java:60` | `fallback("Python LangGraph Agent 返回客户端错误: " + e.getMessage(), traceId)` — Java 异常信息进入 `reason` 字段 |
| `LangGraphAgentController.java:63` | `fallback("Python Agent 服务调用失败: " + e.getMessage(), traceId)` — 同上 |
| `main.py:66` | `reason=str(e)` — Python 异常详情直接返回前端 |

---

## 2. 最小权限模型设计

### 2.1 方案对比

#### 方案 A：Header Token 模式（推荐 ✅）

**机制：**
- 新增配置项 `admin.token`（Java `application.properties`）
- 受保护接口检查请求头 `X-Admin-Token`
- Token 匹配 → 管理员权限；不匹配或缺失 → 普通用户

**优点：**
- 实现简单：一个 Java Filter 或 Controller 内判断
- 不引入用户体系、Session、JWT
- 配置即生效，环境变量覆盖方便
- 本地 Demo 零配置（token 为空时跳过检查）
- 权限判断集中在 Java 后端，符合架构约束

**缺点：**
- 共享 Token，无 per-user 身份
- Token 泄露则所有人可获取管理员权限
- 无法审计"谁"访问了 eval

**适用场景：** 本地 Demo / 开发环境 / 内部工具保护

#### 方案 B：Demo Role Header 模式（不推荐 ❌）

**机制：**
- 前端发送 `X-Demo-Role: ADMIN` 请求头
- Java 后端读取 role 做权限判断

**为什么不安全：**
- 前端 role **完全不可信** — 任何用户可通过 curl/DevTools 修改
- 架构约束明确禁止：`01-architecture-boundary.md` → "Java 后端禁止：❌ 相信前端传来的 role 字段做权限判断"
- 等于没有权限控制

### 2.2 推荐方案：Header Token 模式

**配置：**

```properties
# application.properties
# 管理员 Token，为空则跳过检查（本地 Demo 模式）
# 生产环境必须设置，建议通过环境变量 ADMIN_TOKEN 覆盖
admin.token=
```

**行为规则：**

| `admin.token` 配置 | 请求带 `X-Admin-Token` | 结果 |
|---|---|---|
| 空（Demo 模式） | 任意 | 跳过检查，所有功能可用 |
| 已配置 | 匹配 | 管理员权限 |
| 已配置 | 不匹配或缺失 | 普通用户权限 |

**CORS 支持：** `WebConfig.java` 需将 `X-Admin-Token` 加入 `allowedHeaders`（当前已是 `*`，无需修改，但显式声明更清晰）。

> **⚠️ 安全边界声明：admin.token 为空模式**
>
> - `admin.token` 为空时属于**本地 Demo 便捷模式**，管理员诊断能力（如 eval 查询）默认可用，是为了不影响本地演示和开发调试。
> - 该模式**不具备生产安全性** — 任何人均可访问所有接口，无认证校验。
> - 任何生产化部署都**必须**配置 `admin.token`，或替换为正式认证体系（如 JWT + 用户体系）。
> - 如果 `admin.token` 为空，**不能声明认证授权已达到生产标准**。
> - 本文档所描述的权限方案仅解决 Demo 阶段的最小访问控制，不等价于生产级认证。

---

## 3. Evaluation 访问限制设计

### 3.1 限制策略

**原则：** Java 是唯一权限判断入口。Java 判断后，通过 Header 告知 Python 是否允许 eval 路由。

**流程：**

```
前端 → POST /api/agent/langgraph/chat
  → Java LangGraphAgentController
    → 检查 X-Admin-Token 是否匹配 admin.token
    → 设置 X-Allow-Eval: true/false 请求头
    → 转发给 Python
  → Python /agent/langgraph/chat
    → LangGraph router_node
      → 如果 X-Allow-Eval != true → 跳过 eval 关键词匹配 → 路由到 rag_node
      → 如果 X-Allow-Eval == true → 正常关键词匹配 → 可路由到 eval_node
```

**效果：**
- 普通用户发送"当前RAG评估通过率？"→ 走 RAG 链路，返回知识库问答（不会泄露评估数据）
- 管理员发送相同问题 → 走 eval 链路，返回评估报告
- 前端无感知，无需修改 UI

### 3.2 Python 侧实现要点

`langgraph_agent.py` 的 `router_node` 需要接收 `allow_eval` 状态：

```python
def router_node(state: AgentState) -> dict:
    if not state.get("safe", True):
        return {"route": "refuse"}

    question = state["question"]
    # 仅当 allow_eval=True 时才匹配 eval 关键词
    if state.get("allow_eval") and any(kw in question.lower() for kw in EVAL_KEYWORDS):
        return {"route": "eval"}
    return {"route": "rag"}
```

`main.py` 的 `/agent/langgraph/chat` 端点需要读取 `X-Allow-Eval` header 并传入 `run_langgraph_agent()`。

> **⚠️ 安全边界声明：X-Allow-Eval 不是认证凭证**
>
> - `X-Allow-Eval` **不是认证凭证**，不代表任何用户身份或独立安全判断。
> - `X-Allow-Eval` 只表示 Java 后端已完成管理员权限判断，允许 Python 执行 eval 路由。
> - Python **不应**将 `X-Allow-Eval` 当作独立安全边界 — 它只是 Java 决策的传递信号。
> - 如果 Python 服务被外部直接访问（绕过 Java），攻击者可伪造 `X-Allow-Eval: true` 请求头，直接访问 eval 能力。
> - 因此，Python 服务直接暴露仍属于 **FIX-003** 的范围，后续需要通过部署拓扑 / 内网绑定 / 防火墙 / 反向代理规则解决。
> - **FIX-005 只解决 Evaluation 路由访问限制（经由 Java 入口），不等价于解决 FIX-003（Python 服务裸露）。**

### 3.3 本地脚本不受影响

`scripts/eval/` 下的评估脚本（`run_rag_eval.py` 等）是本地 CLI 工具，不经过 HTTP，不受此限制。

---

## 4. traceId 策略设计

### 4.1 设计原则

- **traceId 仅用于链路追踪，不能用于权限判断**
- **客户端传入的 traceId 不应被直接信任**
- **Java 统一管控 traceId 格式**

### 4.2 处理策略

```
客户端传入 X-Trace-Id
  ↓
Java TraceIdFilter:
  ├─ 为空/缺失 → 生成 UUID ✅
  ├─ 格式合法（UUID） → 接受 ✅
  └─ 格式非法 → 丢弃，重新生成 UUID ✅
  ↓
Java → Python: X-Trace-Id（保证合法格式）
  ↓
Python trace_id_middleware:
  ├─ 有 header → 直接使用（Java 已保证格式）
  └─ 无 header → 生成 UUID（兜底，防止直接调用 Python 场景）
```

### 4.3 UUID 格式验证

标准 UUID v4 格式：`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`

```java
// TraceIdFilter.java
private static final Pattern UUID_PATTERN =
    Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

private String validateOrGenerateTraceId(String traceId) {
    if (traceId != null && !traceId.isBlank() && UUID_PATTERN.matcher(traceId).matches()) {
        return traceId;
    }
    return UUID.randomUUID().toString();
}
```

### 4.4 边界情况

| 场景 | 处理 |
|------|------|
| 前端不传 X-Trace-Id | Java 生成 UUID |
| 前端传合法 UUID | 接受 |
| 前端传 `<script>alert(1)</script>` | 丢弃，重新生成 |
| 前端传超长字符串（>36字符） | 丢弃，重新生成 |
| 前端传含换行符的字符串 | 丢弃，重新生成 |
| Python 被直接调用（无 header） | Python 自行生成 UUID |

---

## 5. 异常信息收敛设计

### 5.1 设计原则

- **用户看到的：** 稳定、可理解的通用文案
- **日志记录的：** 完整异常堆栈 + traceId
- **绝不暴露：** 内部 URL、异常类名、堆栈、API Key、文件路径

### 5.2 Java 侧修改

**`LangGraphAgentController.java`** — 当前 fallback 方法：

```java
// 当前（泄露异常信息）：
return fallback("Python LangGraph Agent 返回客户端错误: " + e.getMessage(), traceId);
return fallback("Python Agent 服务调用失败: " + e.getMessage(), traceId);

// 修改后（通用文案 + 日志详情）：
log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
        traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
return fallback(traceId);  // reason 字段为空或固定文案
```

**fallback 方法签名变更：**

```java
// 当前：
private AgentChatResponse fallback(String reason, String traceId) {
    return new AgentChatResponse(
            "当前 Agent 服务暂时不可用，请稍后重试。",
            "error", true, "error",
            reason,  // ← 泄露到前端
            List.of(), false, traceId);
}

// 修改后：
private AgentChatResponse fallback(String traceId) {
    return new AgentChatResponse(
            "当前 Agent 服务暂时不可用，请稍后重试。",
            "error", true, "error",
            "",  // reason 不暴露异常细节
            List.of(), false, traceId);
}
```

**`ChatController.java`** — 已正确处理（catch 块只返回通用文案，异常详情仅记日志）。✅ 无需修改。

**`GlobalExceptionHandler.java`** — 校验错误返回字段名 + 默认消息，可接受（如 `"message: message 长度不能超过 2000 字符"`）。不暴露堆栈。✅ 无需修改。

### 5.3 Python 侧修改

**`main.py`** `/agent/langgraph/chat` 端点：

```python
# 当前（泄露异常信息）：
except Exception as e:
    logger.exception('[%s] LangGraph Agent 异常', trace_id)
    return AgentResponse(
        ...
        reason=str(e),  # ← 泄露到前端
        ...
    )

# 修改后：
except Exception as e:
    logger.exception('[%s] LangGraph Agent 异常', trace_id)
    return AgentResponse(
        answer='当前 Agent 服务暂时不可用，请稍后重试。',
        route='error', safe=True, category='error',
        reason='',  # 不暴露异常细节
        sources=[], success=False, traceId=trace_id,
    )
```

**`rag_service.py`** — 已正确处理（catch 块返回通用文案）。✅ 无需修改。

### 5.4 异常响应规范

| 场景 | 用户看到的 answer | 用户看到的 reason | 日志记录 |
|------|---|---|---|
| Java 调 Python 超时 | "当前 Agent 服务暂时不可用，请稍后重试。" | `""` | `[traceId] 调用 Python 超时: {异常详情}` |
| Java 调 Python 4xx | "当前 Agent 服务暂时不可用，请稍后重试。" | `""` | `[traceId] Python 返回 HTTP 4xx: status={n}, body={...}` |
| Java 调 Python 未知异常 | "当前 Agent 服务暂时不可用，请稍后重试。" | `""` | `[traceId] 调用 Python 发生未知异常: {堆栈}` |
| Python LangGraph 异常 | "当前 Agent 服务暂时不可用，请稍后重试。" | `""` | `[traceId] LangGraph Agent 异常: {堆栈}` |
| Python LLM 超时 | "当前 AI 服务暂时不可用，请稍后重试。" | — | `[traceId] 调用 LLM 失败: {异常详情}` |
| Java 校验失败 | "请求参数校验失败: {字段: 原因}" | — | `[traceId] 输入校验失败: {原因}` |

---

## 6. 实现任务拆分

### Batch 3 任务总览

| Task ID | 问题 | Owner | 分支 | 修改范围 | 验收标准 | 阻塞生产化 |
|---|---|---|---|---|---|---|
| FIX-002 | 无认证/授权机制 | A1 | feat/batch3-access-control | Java: `application.properties`, `LangGraphAgentController.java`, `WebConfig.java` | `admin.token` 配置项；token 为空时跳过检查；token 已配置时校验 `X-Admin-Token` | ✅ 是 |
| FIX-005 | Evaluation 接口无访问限制 | A1 + A2 | feat/batch3-access-control | Java: `LangGraphAgentController.java`；Python: `main.py`, `langgraph_agent.py` | Java 传递 `X-Allow-Eval` header；Python router_node 读取该 header 决定是否路由到 eval_node | ✅ 是 |
| FIX-016 | traceId 可被伪造 | A1 | feat/batch3-access-control | Java: `TraceIdFilter.java` | 验证 UUID 格式，拒绝非法输入，重新生成 | ❌ 否 |
| FIX-015 | 异常信息暴露到响应 | A1 + A2 | feat/batch3-access-control | Java: `LangGraphAgentController.java`；Python: `main.py` | 异常响应不包含 `e.getMessage()` 或 `str(e)`；详情仅记日志 | ❌ 否 |

---

### FIX-002：Header Token 认证

**Owner:** A1（全栈开发）

**分支:** `feat/batch3-access-control`

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `backend-java/.../application.properties` | 新增 `admin.token=` 配置项 |
| `backend-java/.../controller/LangGraphAgentController.java` | 注入 `admin.token`；`langgraphChat()` 方法开头校验 `X-Admin-Token`；设置 `X-Allow-Eval` header |
| `backend-java/.../config/WebConfig.java` | `allowedHeaders` 显式包含 `X-Admin-Token`、`X-Allow-Eval`（当前已是 `*`，显式声明更清晰） |

**实现逻辑（伪代码）：**

```java
// LangGraphAgentController.java
@Value("${admin.token:}")
private String adminToken;

@PostMapping("/api/agent/langgraph/chat")
public AgentChatResponse langgraphChat(@Valid @RequestBody ChatRequest request,
                                       HttpServletRequest httpRequest) {
    String traceId = (String) httpRequest.getAttribute("traceId");

    // 判断是否为管理员
    boolean isAdmin = !adminToken.isBlank()
            && adminToken.equals(httpRequest.getHeader("X-Admin-Token"));

    // 构建请求头
    HttpHeaders headers = new HttpHeaders();
    headers.setContentType(MediaType.APPLICATION_JSON);
    headers.set("X-Trace-Id", traceId);
    headers.set("X-Allow-Eval", Boolean.toString(isAdmin));

    // ... 转发给 Python（其余逻辑不变）
}
```

**验收标准：**
1. `admin.token` 为空时：所有用户可访问所有功能（包括 eval），行为与修改前一致
2. `admin.token` 已配置时：
   - 不带 `X-Admin-Token` → 普通用户，eval 查询走 RAG 链路
   - 带正确 `X-Admin-Token` → 管理员，eval 查询走 eval 链路
   - 带错误 `X-Admin-Token` → 普通用户
3. `curl` 测试通过

---

### FIX-005：Evaluation 访问限制

**Owner:** A1（Java 侧）+ A2（Python 侧）

**分支:** `feat/batch3-access-control`（与 FIX-002 同分支）

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `agent-python/app/main.py` | `/agent/langgraph/chat` 端点读取 `X-Allow-Eval` header，传入 `run_langgraph_agent()` |
| `agent-python/app/agents/langgraph_agent.py` | `AgentState` 新增 `allow_eval` 字段；`router_node` 根据 `allow_eval` 决定是否匹配 eval 关键词 |

**实现逻辑（伪代码）：**

```python
# main.py
@app.post('/agent/langgraph/chat')
def langgraph_chat(request: ChatRequest, req: Request) -> AgentResponse:
    trace_id = req.state.trace_id
    allow_eval = req.headers.get('x-allow-eval', 'false').lower() == 'true'

    # ... 输入校验（不变）

    try:
        result = run_langgraph_agent(request.message, allow_eval=allow_eval)
        # ... 构建响应（不变）
```

```python
# langgraph_agent.py
def run_langgraph_agent(question: str, allow_eval: bool = False) -> dict:
    graph = build_agent_graph()
    initial: AgentState = {
        "question": question, "safe": True, "route": "",
        "answer": "", "tool_result": {}, "sources": [],
        "reason": "", "category": "",
        "allow_eval": allow_eval,  # 新增
    }
    return dict(graph.invoke(initial))

def router_node(state: AgentState) -> dict:
    if not state.get("safe", True):
        return {"route": "refuse"}

    question = state["question"]
    # 仅当 allow_eval=True 时才匹配 eval 关键词
    if state.get("allow_eval") and any(kw in question.lower() for kw in EVAL_KEYWORDS):
        return {"route": "eval"}
    return {"route": "rag"}
```

**验收标准：**
1. `admin.token` 为空时：eval 查询正常返回评估报告（行为不变）
2. `admin.token` 已配置时：
   - 不带 `X-Admin-Token` → eval 查询走 RAG 链路，返回知识库问答
   - 带正确 `X-Admin-Token` → eval 查询走 eval 链路，返回评估报告
3. `scripts/eval/run_rag_eval.py` 本地脚本不受影响
4. `curl` 测试通过

---

### FIX-016：traceId 格式验证

**Owner:** A1（全栈开发）

**分支:** `feat/batch3-access-control`

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `backend-java/.../filter/TraceIdFilter.java` | 新增 UUID 格式验证；非法格式丢弃并重新生成 |

**实现逻辑（伪代码）：**

```java
// TraceIdFilter.java
private static final Pattern UUID_PATTERN = Pattern.compile(
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
);

@Override
protected void doFilterInternal(...) {
    String traceId = request.getHeader(HEADER);
    if (traceId == null || traceId.isBlank() || !UUID_PATTERN.matcher(traceId).matches()) {
        traceId = UUID.randomUUID().toString();
    }
    // ... 后续不变
}
```

**验收标准：**
1. 不传 `X-Trace-Id` → Java 生成 UUID ✅
2. 传合法 UUID → 接受 ✅
3. 传非法字符串（含 `<script>`、超长、换行符）→ 丢弃，Java 重新生成 ✅
4. 日志中 traceId 始终为合法 UUID 格式

---

### FIX-015：异常信息收敛

**Owner:** A1（Java 侧）+ A2（Python 侧）

**分支:** `feat/batch3-access-control`

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `backend-java/.../controller/LangGraphAgentController.java` | `fallback()` 方法移除 `reason` 参数；catch 块不将 `e.getMessage()` 传入响应 |
| `agent-python/app/main.py` | `/agent/langgraph/chat` 端点 catch 块中 `reason` 改为空字符串 |

**Java 修改：**

```java
// LangGraphAgentController.java

// 修改 catch 块：
} catch (HttpClientErrorException e) {
    log.error("[{}] Python 返回 HTTP 4xx: status={}, body={}",
            traceId, e.getStatusCode(), e.getResponseBodyAsString(), e);
    return fallback(traceId);  // 不传 e.getMessage()
} catch (Exception e) {
    log.error("[{}] 调用 Python 发生未知异常", traceId, e);
    return fallback(traceId);  // 不传 e.getMessage()
}

// 修改 fallback 方法：
private AgentChatResponse fallback(String traceId) {
    return new AgentChatResponse(
            "当前 Agent 服务暂时不可用，请稍后重试。",
            "error", true, "error",
            "",  // reason 为空
            List.of(), false, traceId);
}
```

**Python 修改：**

```python
# main.py - /agent/langgraph/chat 端点
except Exception as e:
    logger.exception('[%s] LangGraph Agent 异常', trace_id)
    return AgentResponse(
        answer='当前 Agent 服务暂时不可用，请稍后重试。',
        route='error',
        safe=True,
        category='error',
        reason='',  # 改为空字符串，不暴露 str(e)
        sources=[],
        success=False,
        traceId=trace_id,
    )
```

**验收标准：**
1. Python 服务不可用时，Java 响应 `reason` 为空字符串
2. Python 异常时，Python 响应 `reason` 为空字符串
3. 异常详情仅出现在服务端日志中
4. 前端不展示任何内部异常细节

---

## 7. 文档更新计划

Batch 3 代码实现完成后，需同步更新以下文档：

| 文档 | 更新内容 |
|------|----------|
| `docs/api.md` | 新增 `X-Admin-Token` 请求头说明；新增 eval 访问控制说明 |
| `docs/architecture.md` | Java 职责新增 Token 校验 + `X-Allow-Eval` 传递；traceId 验证逻辑 |
| `README.md` | 新增 `admin.token` 配置说明 |
| `docs/agent-collaboration/04-task-board.md` | 更新 FIX-002/005/015/016 状态 |
| `docs/agent-collaboration/phase-3-remediation-plan.md` | 更新 FIX-002/005/015/016 状态 |
| `docs/agent-collaboration/dashboard.md` | 更新 Phase 3 进度 |

---

## 8. 风险与约束

### 8.1 设计约束

| 约束 | 说明 |
|------|------|
| 不引入 Spring Security | 仅用 Filter + Controller 内判断，不引入 Security 框架 |
| 不引入用户体系 | 无用户名/密码/Session/JWT |
| 不影响 Demo 启动 | `admin.token` 为空时零配置，所有功能可用 |
| 不声明生产级认证 | 文档明确标注这是 Demo 级保护 |
| 权限判断在 Java | Python 仅根据 Java 传递的 header 行动，不自行判断权限 |

### 8.2 已知局限

| 局限 | 影响 | 后续方案 |
|------|------|----------|
| 共享 Token，无 per-user 身份 | 无法区分用户 | P2: 引入 JWT + 用户体系 |
| Token 泄露则全员管理员 | 安全风险 | 生产环境用 Secrets Manager |
| 无请求频率限制 | 可被暴力调用 | FIX-024: 添加限流 |
| Python 端口 8000 仍可直接访问 | 绕过 Java 层 | FIX-003: 网络层限制 |
| eval 仍可通过 curl + 正确 Token 访问 | 无 IP 限制 | 生产环境加网络层限制 |

### 8.3 不在本次范围

| 项目 | 说明 |
|------|------|
| FIX-003（Python 访问控制） | 需要网络层/部署层配置，单独处理 |
| FIX-018（sources 脱敏） | 独立任务，不阻塞权限方案 |
| FIX-020~025（P2 优化项） | 后续批次 |
| Spring Security 集成 | 生产化阶段再考虑 |
| 用户名/密码登录 | 生产化阶段再考虑 |

---

## 9. 验证方案

### 9.1 本地 Demo 模式验证（admin.token 为空）

```bash
# 启动服务后，不设置 admin.token

# 1. 普通 RAG 问答
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
# 预期：success=true，正常返回

# 2. Agent RAG 问答
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
# 预期：route=rag，正常返回

# 3. Eval 查询（Demo 模式应可用）
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=eval，返回评估报告

# 4. 非法 traceId
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -H "X-Trace-Id: <script>alert(1)</script>" \
  -d '{"message":"测试"}'
# 预期：响应 traceId 为合法 UUID，非注入内容
```

### 9.2 Token 保护模式验证（admin.token 已配置）

```bash
# 设置 admin.token=my-secret-token

# 1. 普通用户访问 eval（无 Token）
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=rag（不是 eval），返回知识库问答

# 2. 管理员访问 eval（带正确 Token）
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=eval，返回评估报告

# 3. 错误 Token
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=rag，返回知识库问答

# 4. 异常场景 — Python 停服
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"测试"}'
# 预期：success=false，reason 为空，不暴露异常细节
```

---

## 10. 任务依赖与执行顺序

```
FIX-002（Token 认证 + X-Allow-Eval）
  ↓ 是 FIX-005 的前置
FIX-005（Python router_node 读取 X-Allow-Eval）
  ↓ 依赖 FIX-002
FIX-016（traceId 格式验证）
  ↓ 无依赖，可与 FIX-002 并行
FIX-015（异常信息收敛）
  ↓ 无依赖，可与 FIX-002 并行
```

**推荐执行顺序：**

1. **FIX-016** — traceId 验证（独立，无依赖，A1 快速完成）
2. **FIX-015** — 异常信息收敛（独立，无依赖，A1 + A2 并行）
3. **FIX-002** — Token 认证 + X-Allow-Eval 传递（A1，核心任务）
4. **FIX-005** — Python router_node 读取 X-Allow-Eval（A2，依赖 FIX-002）

所有任务在同一分支 `feat/batch3-access-control` 上开发，完成后统一合并。
