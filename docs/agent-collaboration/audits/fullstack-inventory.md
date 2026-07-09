# Fullstack Inventory Report

## 1. 基本信息

| 项目 | 值 |
|---|---|
| Agent | A1 Fullstack Engineer |
| Branch | audit/fullstack-inventory |
| 任务类型 | 只读盘点 |
| 是否修改业务代码 | 否 |
| 盘点时间 | 2026-07-10 |

---

## 2. Java 服务盘点

### 2.1 项目目录结构

```
backend-java/src/main/java/com/fantuan/copilot/
├── EnterpriseAiCopilotBackendApplication.java   # Spring Boot 启动类
├── config/
│   ├── RestClientConfig.java                     # RestTemplate + RestClient Bean
│   └── WebConfig.java                            # CORS 配置
├── controller/
│   ├── AgentHealthController.java                # GET /api/agent/health
│   ├── ChatController.java                       # POST /api/chat
│   ├── HealthController.java                     # GET /api/health
│   └── LangGraphAgentController.java            # POST /api/agent/langgraph/chat
├── dto/
│   ├── AgentChatResponse.java                    # Agent 链路响应 DTO
│   ├── ChatRequest.java                          # 请求 DTO
│   └── ChatResponse.java                         # RAG 链路响应 DTO
└── filter/
    └── TraceIdFilter.java                        # traceId 全链路过滤器
```

**依赖（pom.xml）：** spring-boot-starter-web, spring-boot-starter-validation, lombok, spring-boot-starter-test

**配置（application.properties）：**
- `python.agent.base-url=http://localhost:8000`
- 日志格式含 `[%X{traceId}]`

### 2.2 Controller 清单

| Controller | 方法 | 路径 | 说明 |
|---|---|---|---|
| HealthController | GET | `/api/health` | Java 服务健康检查，返回 `{"service":"backend-java","status":"UP"}` |
| AgentHealthController | GET | `/api/agent/health` | 代理 Python 健康检查，使用 RestClient 转发 |
| ChatController | POST | `/api/chat` | 稳定 RAG 主链路，代理 Python `/agent/chat` |
| LangGraphAgentController | POST | `/api/agent/langgraph/chat` | 实验 Agent 链路，代理 Python `/agent/langgraph/chat` |

### 2.3 Service 清单

**无独立 Service 类。** Controller 直接通过 RestTemplate 调用 Python 服务，无中间 Service 层。

### 2.4 DTO / Response 类清单

| 类名 | 类型 | 字段 | 说明 |
|---|---|---|---|
| ChatRequest | record | `message` | 请求体 |
| ChatResponse | record | `answer, model, traceId, success` | RAG 链路响应 |
| AgentChatResponse | record | `answer, route, safe, category, reason, sources, success, traceId` | Agent 链路响应 |

**注意：** AgentChatResponse 使用 `@JsonIgnoreProperties(ignoreUnknown = true)` 忽略 Python 侧未知字段。

### 2.5 Java 调 Python 的实现位置

| 调用方 | HTTP 客户端 | 目标 URL | 说明 |
|---|---|---|---|
| ChatController.chat() | RestTemplate | `{base-url}/agent/chat` | POST, 透传 X-Trace-Id |
| LangGraphAgentController.langgraphChat() | RestTemplate | `{base-url}/agent/langgraph/chat` | POST, 透传 X-Trace-Id |
| AgentHealthController.agentHealth() | RestClient | `http://localhost:8000/agent/health` | GET, 硬编码地址 |

**问题：** AgentHealthController 硬编码了 `http://localhost:8000`，未使用 `python.agent.base-url` 配置。

### 2.6 traceId 相关实现

**TraceIdFilter（`filter/TraceIdFilter.java`）：**
1. 从请求头 `X-Trace-Id` 读取，为空则生成 UUID
2. 存入 SLF4J MDC（日志自动带 traceId）
3. 存入 request attribute（Controller 可取用）
4. 设置响应头 `X-Trace-Id`
5. finally 块清理 MDC（防线程复用污染）

**日志格式：** `%d{HH:mm:ss.SSS} [%X{traceId}] %-5level %logger{36} - %msg%n`

**链路：** Frontend 生成 → Header → Java TraceIdFilter → MDC + attribute + 透传 header → Python middleware → 响应返回

### 2.7 异常兜底实现

| 场景 | ChatController | LangGraphAgentController |
|---|---|---|
| Python 返回 4xx | 返回 `ChatResponse(success=false, model="unknown")` | 返回 `AgentChatResponse(route="error", success=false)` |
| Python 不可达 / 未知异常 | 同上 | 同上 |
| 错误消息 | "当前 AI 服务暂时不可用，请稍后重试。" | "当前 Agent 服务暂时不可用，请稍后重试。" |

**特点：** 任何异常都不会抛到前端，始终返回合法 JSON + traceId。

### 2.8 登录 / 权限 / 角色控制

**无。** 当前没有任何认证、授权、角色控制机制。所有接口公开可访问。

### 2.9 当前 API 与 docs/api.md 一致性

| 接口 | docs/api.md | 实际代码 | 一致性 |
|---|---|---|---|
| GET /api/health | `{"service":"backend-java","status":"UP"}` | `Map.of("service","backend-java","status","UP")` | ✅ 一致 |
| GET /api/agent/health | 转发 Python 响应 | RestClient 转发 | ✅ 一致 |
| POST /api/chat 请求 | `{"message":"..."}` | `ChatRequest(message)` | ✅ 一致 |
| POST /api/chat 响应 | `answer, model, traceId, success` | `ChatResponse(answer, model, traceId, success)` | ✅ 一致 |
| POST /api/agent/langgraph/chat 请求 | `{"message":"..."}` | `ChatRequest(message)` | ✅ 一致 |
| POST /api/agent/langgraph/chat 响应 | `answer, route, safe, category, reason, sources, success, traceId` | `AgentChatResponse(...)` | ✅ 一致 |

**结论：** `docs/api.md` 与实际代码一致。

**⚠️ 但 `02-api-contract.md` 存在偏差（见第 4 节）。**

### 2.10 Java 侧生产化缺口

| 缺口 | 严重度 | 说明 |
|---|---|---|
| 无认证/授权 | 🔴 高 | 所有接口公开可访问 |
| 无请求校验 | 🟡 中 | ChatRequest 的 message 无长度/格式校验 |
| 无 RestTemplate 超时配置 | 🟡 中 | Python 慢响应会阻塞 Java 线程 |
| 无熔断/降级 | 🟡 中 | Python 宕机时无快速失败机制 |
| 无限流 | 🟡 中 | 无请求频率控制 |
| 无单元测试 | 🟡 中 | 仅有 contextLoads 测试 |
| CORS 允许所有来源 | 🟡 中 | `allowedOriginPatterns("*")` |
| AgentHealthController 硬编码地址 | 🟢 低 | 未使用配置化的 base-url |
| 无 Actuator / 健康指标 | 🟢 低 | 无 Python 依赖健康检查指标 |
| 无统一错误码 | 🟢 低 | 错误消息硬编码在 Controller 中 |

---

## 3. Frontend 盘点

### 3.1 前端目录结构

```
frontend/src/
├── App.css          # 主样式（全局，无模块化）
├── App.jsx          # 主组件（所有逻辑集中在一个文件）
├── assets/
│   ├── hero.png
│   ├── react.svg
│   └── vite.svg
├── index.css        # 入口样式
└── main.jsx         # React 入口（StrictMode）
```

**技术栈：** React 19, Vite 8, 无路由库, 无状态管理库, 无 UI 组件库

### 3.2 页面清单

| 页面 | 路径 | 说明 |
|---|---|---|
| App（唯一页面） | `/` | 单页应用，所有功能在一个组件中 |

无路由，无多页面。

### 3.3 API 调用位置

| 调用位置 | 接口 | 触发条件 |
|---|---|---|
| `App.jsx` → `sendMessage()` | `POST /api/agent/langgraph/chat` | mode=agent 时 |
| `App.jsx` → `sendMessage()` | `POST /api/chat` | mode=rag 时 |

使用原生 `fetch` API，无 axios 等封装。

### 3.4 是否只调用 Java API

**是。** 前端通过 Vite proxy 将 `/api` 请求转发到 `http://localhost:8080`。

```javascript
// vite.config.js
proxy: {
  '/api': {
    target: 'http://localhost:8080',
    changeOrigin: true,
  },
}
```

`JAVA_BASE_URL = ''`（空字符串），所有请求走相对路径 → Vite proxy → Java。

### 3.5 是否直接调用 Python API

**否。** 前端不直接调用 `localhost:8000`，符合架构约束。

### 3.6 sources / traceId / route / safe 展示情况

| 字段 | 展示方式 | 模式 |
|---|---|---|
| sources | 有序列表，显示数量和 chunk ID | Agent 模式 |
| traceId | 灰色标签 | 两种模式 |
| route | 彩色标签（rag=蓝, eval=紫, refuse=红） | Agent 模式 |
| safe | 彩色标签（true=绿, false=红） | Agent 模式 |
| category | 橙色标签（仅非 normal 时显示） | Agent 模式 |
| reason | 文本展示 | Agent 模式 |
| model | 蓝色标签 | RAG 模式 |
| success | 彩色标签 | 两种模式 |

### 3.7 loading / error 状态

| 状态 | 实现方式 | 说明 |
|---|---|---|
| Loading | 按钮文字变为"请求中..."，disabled | 无骨架屏/spinner |
| Error | 红色错误横幅，显示错误信息和 traceId | 有基本错误展示 |
| 成功 | 结果卡片展示 | 完整展示所有字段 |

### 3.8 当前前端生产化缺口

| 缺口 | 严重度 | 说明 |
|---|---|---|
| 无 Error Boundary | 🔴 高 | React 渲染异常会导致白屏 |
| 无路由 | 🟡 中 | 单页面，无法扩展多页面 |
| 无会话历史 | 🟡 中 | 刷新页面丢失所有对话 |
| 无输入校验 | 🟡 中 | 仅检查非空，无长度限制 |
| 无重试机制 | 🟡 中 | 请求失败需手动重试 |
| 无响应式优化 | 🟢 低 | 基本可用但未做移动端适配 |
| 无无障碍支持 | 🟢 低 | 缺少 ARIA 标签 |
| 无单元测试 | 🟢 低 | 无任何测试 |
| 无 i18n | 🟢 低 | 硬编码中文 |
| CSS 无模块化 | 🟢 低 | 全局样式，无 CSS Modules |

---

## 4. API 契约一致性检查

### 4.1 docs/api.md vs 实际代码

**结论：一致。** `docs/api.md` 准确描述了实际接口行为。

### 4.2 02-api-contract.md vs 实际代码

**结论：存在偏差。**

| 接口 |02-api-contract.md 描述 | 实际代码 | 偏差 |
|---|---|---|---|
| POST /api/chat 响应 | 包含 `sources` 字段 | `ChatResponse` 无 `sources` | ❌ 契约文档错误 |
| POST /api/agent/langgraph/chat 响应 | 仅 `answer, model, traceId, success` | 实际有 `route, safe, category, reason, sources` 等 | ❌ 契约文档过时 |

**建议：** 更新 `02-api-contract.md`，使其与 `docs/api.md` 和实际代码对齐。

### 4.3 Java DTO vs Python Schema 对比

| 接口 | Java DTO | Python Schema | 一致性 |
|---|---|---|---|
| /agent/chat 请求 | `ChatRequest(message)` | `ChatRequest(message: str)` | ✅ |
| /agent/chat 响应 | `ChatResponse(answer, model, traceId, success)` | `ChatResponse(answer, model, traceId, success)` | ✅ |
| /agent/langgraph/chat 响应 | `AgentChatResponse(answer, route, safe, category, reason, sources, success, traceId)` | `AgentResponse(answer, route, safe, category, reason, sources, success, traceId)` | ✅ |

**注意：** Java 使用 `@JsonIgnoreProperties(ignoreUnknown = true)` 做了防御，Python 新增字段不会导致 Java 反序列化失败。但 Java 新增字段 Python 不返回时，Java 侧会收到 null。

---

## 5. 当前缺失能力

| 能力 | 影响范围 | 优先级 |
|---|---|---|
| 认证/授权 | 全栈 | 🔴 P0 |
| 请求校验 | Java | 🟡 P1 |
| RestTemplate 超时配置 | Java | 🟡 P1 |
| 熔断/降级 | Java | 🟡 P1 |
| 限流 | Java | 🟡 P1 |
| Error Boundary | Frontend | 🟡 P1 |
| 单元测试 | Java + Frontend | 🟡 P1 |
| 统一错误码 | Java | 🟢 P2 |
| 前端路由 | Frontend | 🟢 P2 |
| 会话历史 | Frontend | 🟢 P2 |
| CSS 模块化 | Frontend | 🟢 P2 |

---

## 6. 风险点

| 风险 | 等级 | 说明 |
|---|---|---|
| DTO 契约无强类型保障 | 🟡 中 | Java ↔ Python 手动对齐，改一端另一端不会编译报错 |
| 02-api-contract.md 与代码不一致 | 🟡 中 | 新开发可能参考过时契约文档 |
| AgentHealthController 硬编码地址 | 🟢 低 | 与 ChatController 使用不同地址源 |
| RestTemplate 无超时 | 🟡 中 | Python 慢响应会阻塞 Java 线程池 |
| 前端单组件架构 | 🟢 低 | 当前规模可接受，扩展性差 |
| CORS 允许所有来源 | 🟡 中 | 生产环境需收紧 |

---

## 7. 建议后续任务

| 任务 | 优先级 | 说明 |
|---|---|---|
| 更新02-api-contract.md | 🔴 P0 | 对齐实际代码和 docs/api.md |
| RestTemplate 添加超时配置 | 🟡 P1 | 防止 Python 慢响应阻塞 Java |
| ChatRequest 添加校验注解 | 🟡 P1 | message 非空、长度限制 |
| 前端添加 Error Boundary | 🟡 P1 | 防止 React 异常白屏 |
| AgentHealthController 使用配置地址 | 🟢 P2 | 统一使用 python.agent.base-url |
| 前端拆分组件 | 🟢 P2 | 将 App.jsx 拆分为 ChatInput, ResultCard 等 |
| 添加 Java 单元测试 | 🟢 P2 | Controller 核心逻辑覆盖 |

---

## 8. 不建议做的事项

| 事项 | 原因 |
|---|---|
| 在 Java 侧实现 RAG 逻辑 | 违反架构边界，Python 是 AI 能力层 |
| 前端直接调用 Python API | 违反架构约束（06-do-not-touch.md） |
| 默认启用 hybrid_rerank | 实验模式，当前评估集提升不显著 |
| 默认启用 rewrite_mode=rule | 实验模式，规则有限 |
| 修改知识库文档 | 影响 eval 结果 |
| 在 main 分支直接开发 | 必须用 feature 分支 |
| 提交 .env 或 API Key | 安全约束 |

---

## 9. 是否建议进入开发阶段

**建议：可以进入开发阶段，但需先完成以下前置工作：**

1. **更新02-api-contract.md**：当前契约文档与实际代码存在偏差，会影响开发参考
2. **确认开发优先级**：建议从 P1 任务开始（超时配置、请求校验、Error Boundary）
3. **建立验证流程**：Java 改动需 `./mvnw compile`，Frontend 改动需 `npm run dev`

**当前代码质量评估：**
- Java 侧：结构清晰，职责单一，但缺乏防御性编程（超时、校验）
- Frontend 侧：功能完整，但架构单薄（单组件、无路由）
- 整体：作为 Demo 阶段可接受，进入生产化需要补强基础设施

**不建议做的事：**
- 不建议同时改 Java + Python + Frontend（跨层变更风险高）
- 不建议重构现有稳定链路（/api/chat 是稳定主链路）
- 不建议引入新框架/新依赖（除非有明确收益）
