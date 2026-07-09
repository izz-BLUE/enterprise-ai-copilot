# Security Review Report

## 1. 基本信息

| 项目 | 值 |
|---|---|
| Agent | A4 安全审查 |
| Branch | audit/security-review |
| 任务类型 | 安全审查 / 只读 Review |
| 是否修改业务代码 | 否 |
| 审查时间 | 2026-07-10 |
| 任务 ID | TASK-022 |

---

## 2. 读取文件清单

| # | 文件路径 | 用途 |
|---|---|---|
| 1 | `README.md` | 项目全貌、功能列表、安全说明 |
| 2 | `docs/local-demo-guide.md` | 本地演示指南、环境变量配置 |
| 3 | `docs/demo-script.md` | 演示脚本、Safety Guard 演示 |
| 4 | `docs/rag-quality-engineering.md` | RAG 质量工程文档 |
| 5 | `docs/api.md` | 接口文档、请求响应格式 |
| 6 | `docs/architecture.md` | 架构说明、模块职责 |
| 7 | `docs/agent-collaboration/00-project-context.md` | 项目上下文 |
| 8 | `docs/agent-collaboration/01-architecture-boundary.md` | 架构边界约束 |
| 9 | `docs/agent-collaboration/02-api-contract.md` | API 契约 |
| 10 | `docs/agent-collaboration/03-agent-registry.md` | Agent 注册表 |
| 11 | `docs/agent-collaboration/04-task-board.md` | 任务看板 |
| 12 | `docs/agent-collaboration/06-do-not-touch.md` | 不可修改清单 |
| 13 | `docs/agent-collaboration/07-release-checklist.md` | 发布检查清单 |
| 14 | `docs/agent-collaboration/dashboard.md` | 协作仪表盘 |
| 15 | `docs/agent-collaboration/audits/fullstack-inventory.md` | 全栈盘点报告 |
| 16 | `docs/agent-collaboration/audits/ai-rag-inventory.md` | AI/RAG 盘点报告 |
| 17 | `.gitignore` | Git 忽略规则 |
| 18 | `agent-python/app/core/config.py` | Python 配置 |
| 19 | `agent-python/app/guards/safety_guard.py` | Safety Guard 实现 |
| 20 | `agent-python/app/main.py` | FastAPI 入口 |
| 21 | `agent-python/app/services/rag_service.py` | RAG 主服务 |
| 22 | `agent-python/app/services/llm_service.py` | LLM 调用服务 |
| 23 | `agent-python/app/prompts/system_prompt.py` | Prompt 模板 |
| 24 | `agent-python/app/agents/langgraph_agent.py` | LangGraph Agent |
| 25 | `agent-python/app/tools/rag_tools.py` | Agent Tools |
| 26 | `backend-java/src/main/java/com/fantuan/copilot/config/WebConfig.java` | CORS 配置 |
| 27 | `backend-java/src/main/java/com/fantuan/copilot/filter/TraceIdFilter.java` | traceId 过滤器 |
| 28 | `backend-java/src/main/java/com/fantuan/copilot/controller/ChatController.java` | RAG Controller |
| 29 | `backend-java/src/main/java/com/fantuan/copilot/controller/LangGraphAgentController.java` | Agent Controller |
| 30 | `frontend/src/App.jsx` | 前端主组件 |

---

## 3. 审查范围

本次安全审查覆盖以下维度：

1. **API Key / Secret 风险** — 密钥管理、泄露风险、日志暴露
2. **权限边界** — 认证授权、角色控制、接口访问控制
3. **Java ↔ Python 服务边界** — 服务隔离、访问控制、超时配置
4. **Prompt Injection / RAG 安全** — Safety Guard 覆盖范围、绕过风险
5. **文件与知识库安全** — 路径穿越、内容注入风险
6. **CORS / 前端风险** — 跨域配置、信息泄露
7. **日志风险** — 敏感信息打印、脱敏机制

---

## 4. 高风险问题 P0

> 以下问题可能阻塞生产化部署，建议在进入开发修复阶段前优先处理。

### P0-1: CORS 配置过宽

**问题：** `WebConfig.java:12` 使用 `allowedOriginPatterns("*")` 允许任意来源访问。

**风险：**
- 生产环境可被恶意网站发起 CSRF 攻击
- 配合 `allowCredentials(true)` 可能窃取用户会话

**代码位置：**
```java
// backend-java/src/main/java/com/fantuan/copilot/config/WebConfig.java:12
.allowedOriginPatterns("*")
```

**修复建议：**
- 生产环境必须限制为具体域名
- 开发环境可通过配置文件指定允许的来源

---

### P0-2: 无认证/授权机制

**问题：** 所有 API 端点（包括评估报告查询）均无任何认证或授权控制。

**风险：**
- 任何用户可访问所有接口
- 评估报告可被任意用户查询（泄露内部质量数据）
- 无用户身份追踪，无法审计

**影响范围：**
- `POST /api/chat` — RAG 问答
- `POST /api/agent/langgraph/chat` — Agent 问答 + 评估查询
- `GET /api/health` — 健康检查
- `GET /api/agent/health` — Python 服务健康检查

**修复建议：**
- 至少实现 API Key 或 JWT 认证
- 评估接口应限制为开发者/管理员角色
- 健康检查接口可保持公开（用于负载均衡器探测）

---

### P0-3: Python 服务可被直接访问

**问题：** Python FastAPI 服务（端口 8000）在架构上被定位为内部服务，但技术上无任何访问控制，可被直接访问。

**风险：**
- 绕过 Java 业务层的安全检查
- 绕过 Safety Guard（仅 Agent 链路有）
- 直接访问 LLM 调用能力

**架构约束文档：**
```
// docs/agent-collaboration/01-architecture-boundary.md
// Python 服务禁止：❌ 直接暴露给前端
```

**修复建议：**
- 生产环境 Python 服务应绑定 localhost，仅允许 Java 服务访问
- 或通过网络层（防火墙/iptables）限制访问来源
- 或在 Python 服务添加内部 API Key 认证

---

### P0-4: Safety Guard 仅覆盖 Agent 链路

**问题：** Safety Guard 仅在 LangGraph Agent 链路（`/agent/langgraph/chat`）生效，RAG 主链路（`/agent/chat`）无任何输入安全检查。

**风险：**
- 用户可通过 RAG 主链路绕过 Safety Guard
- 伪造病假材料、攻击系统等恶意请求在 RAG 链路无拦截

**对比：**

| 链路 | Safety Guard | 路径 |
|---|---|---|
| RAG 主链路 | ❌ 无 | `/api/chat` → `/agent/chat` |
| Agent 链路 | ✅ 有 | `/api/agent/langgraph/chat` → `/agent/langgraph/chat` |

**修复建议：**
- 将 Safety Guard 逻辑提取为公共模块
- 在 RAG 主链路入口处也应用安全检查
- 或统一在 Java 层做输入安全检查

---

### P0-5: Evaluation 接口无访问限制

**问题：** 评估报告查询通过 Agent 链路的 `eval_node` 实现，任何用户可通过发送包含"评估"关键词的消息访问。

**风险：**
- 泄露内部质量数据（通过率、失败率、flaky 数量）
- 暴露评估体系细节（case 数量、baseline 状态）

**触发方式：**
```bash
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```

**修复建议：**
- 评估接口应仅限开发者/管理员访问
- 生产环境应禁用或移除 eval_node
- 或添加角色判断逻辑

---

## 5. 中风险问题 P1

> 以下问题建议进入下一轮修复，不阻塞当前 Demo 阶段。

### P1-1: 日志打印完整用户问题

**问题：** 多处日志打印完整用户问题文本，可能泄露用户隐私。

**代码位置：**
```python
# agent-python/app/main.py:35
logger.info('[%s] 收到普通 RAG 请求: %s', trace_id, request.message)

# agent-python/app/services/rag_service.py:31
logger.info('[%s] 用户问题: %s | 检索 query: %s | 命中 chunk: %d',
            trace_id, message, retrieval_query, len(chunks))
```

```java
// backend-java/src/main/java/com/fantuan/copilot/controller/ChatController.java:37
log.info("[{}] 收到普通 RAG 请求: {}", traceId, request.message());

// backend-java/src/main/java/com/fantuan/copilot/controller/LangGraphAgentController.java:39
log.info("[{}] 收到 LangGraph Agent 请求: {}", traceId, request.message());
```

**风险：**
- 生产环境日志可能包含敏感业务问题
- 日志文件可能被未授权人员访问

**修复建议：**
- 日志中对用户问题做截断或脱敏
- 生产环境仅打印问题摘要（前 20 字符）
- 区分 debug / prod 日志级别

---

### P1-2: 异常信息暴露到响应

**问题：** 异常处理时将错误详情返回给前端。

**代码位置：**
```python
# agent-python/app/main.py:56
return AgentResponse(
    ...
    reason=str(e),  # 异常详情直接返回
    ...
)
```

```java
// backend-java/src/main/java/com/fantuan/copilot/controller/LangGraphAgentController.java:59
return fallback("Python LangGraph Agent 返回客户端错误: " + e.getMessage(), traceId);
```

**风险：**
- 暴露内部实现细节（文件路径、堆栈信息）
- 可能泄露 Python 服务地址、模块结构

**修复建议：**
- 前端仅显示通用错误消息
- 详细错误信息记录到日志
- 使用 traceId 关联前后端日志

---

### P1-3: Safety Guard 仅关键词匹配

**问题：** Safety Guard 使用关键词匹配，无法处理变体表达、谐音、英文等绕过方式。

**风险：**
- 可通过同音字绕过（如"仿造"代替"伪造"）
- 可通过英文绕过（如"fake sick leave"）
- 可通过拼音绕过（如"weizao"）
- 可通过插入空格/特殊字符绕过

**当前覆盖的 5 类风险：**

| 类别 | 关键词数量 |
|---|---|
| `illegal_or_policy_violation` | 9 |
| `policy_bypass` | 13 |
| `cybersecurity_attack` | 12 |
| `audit_tampering` | 7 |
| `unauthorized_access` | 9 |

**修复建议：**
- 补充同音字、拼音变体关键词
- 考虑引入正则表达式匹配
- 长期考虑 LLM-based 安全检查

---

### P1-4: RestTemplate 无超时配置

**问题：** Java 调用 Python 服务时未配置连接超时和读取超时。

**风险：**
- Python 服务慢响应会阻塞 Java 线程池
- 可能导致级联故障

**影响范围：**
- `ChatController.chat()`
- `LangGraphAgentController.langgraphChat()`

**修复建议：**
- 配置连接超时（建议 3-5 秒）
- 配置读取超时（建议 30-60 秒，考虑 LLM 调用时间）
- 考虑引入熔断机制

---

### P1-5: traceId 可被伪造

**问题：** 前端生成的 traceId 被信任并透传到日志系统，可能被注入恶意内容。

**代码位置：**
```javascript
// frontend/src/App.jsx:49
const traceId = crypto.randomUUID()
  ? crypto.randomUUID()
  : Date.now() + '-' + Math.random().toString(36).slice(2)
```

```java
// backend-java/src/main/java/com/fantuan/copilot/filter/TraceIdFilter.java:27
String traceId = request.getHeader(HEADER);
if (traceId == null || traceId.isBlank()) {
    traceId = UUID.randomUUID().toString();
}
```

**风险：**
- 可注入恶意内容到日志（如换行符、控制字符）
- 可伪造 traceId 进行日志关联攻击

**修复建议：**
- Java 层验证 traceId 格式（UUID 格式）
- 拒绝或重新生成不符合格式的 traceId
- 对 traceId 做转义处理

---

### P1-6: 无请求大小限制

**问题：** ChatRequest 的 message 字段无长度限制。

**风险：**
- 超长输入可能导致内存溢出
- 可能导致 LLM 调用超时或费用激增
- 可能用于 DoS 攻击

**修复建议：**
- Java 层添加请求体大小限制
- message 字段添加长度校验（建议最大 1000 字符）
- 添加全局请求体大小限制

---

### P1-7: sources 字段暴露内部路径

**问题：** 前端展示的 sources 包含内部文件名（如 `hr_leave_policy_real_sample_010`）。

**风险：**
- 暴露内部知识库结构和文件命名规则
- 可推测文档数量和组织方式

**修复建议：**
- 考虑对 sources 做脱敏或映射
- 或仅展示文档标题而非文件名

---

## 6. 低风险问题 P2

> 以下问题为后续优化项，不阻塞当前阶段。

### P2-1: API Key 通过环境变量管理

**现状：** API Key 通过 `.env` 文件和环境变量管理，`.gitignore` 已排除 `.env`。

**评估：** 当前方式可接受，但生产环境建议使用 Secrets Manager（如 HashiCorp Vault、AWS Secrets Manager）。

---

### P2-2: 日志格式未区分环境

**现状：** 使用统一的 `logging.basicConfig(level=logging.INFO)`。

**建议：**
- 生产环境使用 JSON 格式日志
- 区分 debug / info / warn / error 级别
- 敏感字段脱敏

---

### P2-3: 无请求频率限制

**现状：** 无任何限流机制。

**建议：**
- 生产环境添加 API 限流（如 100 次/分钟/用户）
- 防止恶意调用和费用超支

---

### P2-4: AgentHealthController 硬编码地址

**问题：** `AgentHealthController` 硬编码了 `http://localhost:8000`，未使用配置化的 `python.agent.base-url`。

**代码位置：**
```
// backend-java/src/main/java/com/fantuan/copilot/controller/AgentHealthController.java
// 硬编码 http://localhost:8000
```

**建议：** 统一使用配置文件中的地址。

---

### P2-5: 无输入长度校验

**现状：** ChatRequest 的 message 字段无长度限制。

**建议：** 添加 `@Size(max=1000)` 注解。

---

## 7. 权限边界结论

### 7.1 普通用户是否应该访问 Evaluation

**结论：不应该。**

评估报告包含内部质量数据（通过率、失败案例、flaky 检测），属于开发/运维信息，不应暴露给普通用户。

**当前状态：** 任何用户可通过 Agent 链路查询评估报告。

**建议：**
- 生产环境禁用 eval_node
- 或添加角色判断，仅允许开发者/管理员访问

---

### 7.2 前端 role 是否可信

**结论：当前无 role 机制，此问题不适用。**

根据 `01-architecture-boundary.md`：
> Java 后端禁止：❌ 相信前端传来的 `role` 字段做权限判断

当前项目未实现 role 机制，但架构约束已明确：**权限判断必须在 Java 后端完成**。

---

### 7.3 Java 后端是否应作为权限判断唯一入口

**结论：是。**

架构设计明确：
- Java 是唯一的对外入口
- Python 是内部能力层
- 权限判断必须在 Java 后端完成

**当前问题：** Java 层未实现任何权限判断。

---

### 7.4 Python 是否应该只作为内部服务

**结论：是。**

架构设计明确：
- Python 服务定位为内部 AI 能力层
- 禁止直接暴露给前端
- 前端必须通过 Java 代理访问

**当前问题：** Python 服务技术上可被直接访问（端口 8000 无访问控制）。

---

## 8. Prompt Injection 结论

### 8.1 Safety Guard 覆盖范围

**当前覆盖：**
- ✅ 5 类风险关键词匹配（50 个关键词）
- ✅ 空查询拦截
- ✅ 仅 Agent 链路生效

**覆盖的风险类别：**

| 类别 | 说明 | 关键词数 |
|---|---|---|
| `illegal_or_policy_violation` | 违法违规 / 伪造材料 | 9 |
| `policy_bypass` | 绕过企业制度 / 规避审批 | 13 |
| `cybersecurity_attack` | 网络安全攻击 / 黑客行为 | 12 |
| `audit_tampering` | 删除审计 / 隐藏痕迹 | 7 |
| `unauthorized_access` | 越权访问 / 数据窃取 | 9 |

### 8.2 未覆盖的风险

1. **RAG 主链路无 Safety Guard** — 用户可绕过 Agent 链路直接调用 RAG 主链路
2. **Prompt Injection 攻击** — 未检测"忽略系统提示词"、"泄露 system prompt"等注入尝试
3. **变体绕过** — 仅关键词匹配，无法处理同音字、拼音、英文等变体
4. **间接注入** — 未检测通过知识库文档注入的恶意内容
5. **越权回答诱导** — 未检测诱导模型回答超出知识库范围的问题

### 8.3 RAG Prompt 安全设计

**当前 RAG Prompt 包含的安全规则：**
- ✅ 不编造没有依据的信息
- ✅ 知识库无相关内容时明确拒答
- ✅ 保留制度原文表述
- ✅ 多来源差异标注

**缺失的安全规则：**
- ❌ 未明确禁止"忽略系统提示词"类攻击
- ❌ 未明确禁止泄露 system prompt
- ❌ 未明确限制回答范围（仅限企业制度）

---

## 9. API Key / Secret 结论

### 9.1 硬编码密钥检查

**结论：未发现硬编码密钥。**

- ✅ `.env` 文件未被提交（`.gitignore` 已排除）
- ✅ 代码中未发现硬编码 API Key
- ✅ `.env.example` 未被发现（无泄露风险）

### 9.2 环境变量风险

**现状：**
- API Key 通过 `DEEPSEEK_API_KEY` 环境变量加载
- `.env` 文件本地存储
- `config.py` 未打印 API Key

**评估：** 当前方式在 Demo 阶段可接受。

**生产环境建议：**
- 使用 Secrets Manager
- 定期轮换 API Key
- 限制 API Key 权限范围

### 9.3 日志泄露风险

**结论：日志未打印 API Key。**

检查范围：
- ✅ `config.py` — 未打印 API Key
- ✅ `llm_service.py` — 未打印 API Key
- ✅ `rag_service.py` — 未打印 API Key
- ✅ `ChatController.java` — 未打印 API Key
- ✅ `LangGraphAgentController.java` — 未打印 API Key

**但存在其他日志风险：**
- ⚠️ 打印完整用户问题（见 P1-1）
- ⚠️ 打印 Python 服务 URL（可能暴露内部地址）

---

## 10. 建议修复任务

### A1-全栈开发

| 任务 | 优先级 | 说明 |
|---|---|---|
| CORS 配置收紧 | 🔴 P0 | 生产环境限制为具体域名 |
| RestTemplate 超时配置 | 🟡 P1 | 添加连接超时和读取超时 |
| 请求大小限制 | 🟡 P1 | 限制 message 字段长度 |
| traceId 格式验证 | 🟡 P1 | 验证 UUID 格式，拒绝恶意输入 |
| AgentHealthController 修复 | 🟢 P2 | 使用配置化地址 |
| 异常信息脱敏 | 🟡 P1 | 不返回详细异常到前端 |

### A2-AI/RAG

| 任务 | 优先级 | 说明 |
|---|---|---|
| Safety Guard 扩展到 RAG 主链路 | 🔴 P0 | 统一安全检查入口 |
| Safety Guard 增强 | 🟡 P1 | 补充同音字、拼音、英文变体 |
| Prompt Injection 防护 | 🟡 P1 | 添加"忽略系统提示词"等检测 |
| RAG Prompt 安全规则补充 | 🟡 P1 | 明确禁止泄露 system prompt |
| 日志脱敏 | 🟡 P1 | 用户问题截断或脱敏 |
| eval_node 访问控制 | 🔴 P0 | 生产环境禁用或添加角色判断 |

### A0-架构负责人

| 任务 | 优先级 | 说明 |
|---|---|---|
| 认证授权方案设计 | 🔴 P0 | 设计 API Key / JWT 认证方案 |
| Python 服务访问控制 | 🔴 P0 | 设计网络层或应用层访问控制 |
| 权限角色设计 | 🟡 P1 | 设计开发者/管理员/普通用户角色 |
| 安全审查流程 | 🟢 P2 | 建立定期安全审查机制 |

### A5-部署交付

| 任务 | 优先级 | 说明 |
|---|---|---|
| Python 服务绑定 localhost | 🔴 P0 | 生产环境仅允许本地访问 |
| 防火墙规则配置 | 🔴 P0 | 限制端口访问来源 |
| Secrets Manager 集成 | 🟢 P2 | 生产环境使用 Vault/AWS SM |
| 日志收集与审计 | 🟡 P1 | 集中日志、审计追踪 |

---

## 11. 是否建议进入开发修复阶段

### 结论：**有条件建议**

**理由：**

1. **当前阶段定位为 Demo** — 项目明确为"本地可复现的 RAG 应用后端 Demo"，非生产系统
2. **P0 问题在 Demo 场景下风险可控** — 本地运行、无公网暴露、无真实用户数据
3. **但存在明确的生产化阻塞项** — 如果要部署公网，必须先解决 P0 问题

**建议：**

1. **Demo 阶段** — 可以继续开发，但应在文档中明确标注安全限制
2. **生产化前** — 必须完成以下 P0 修复：
   - 认证授权机制
   - CORS 配置收紧
   - Python 服务访问控制
   - Safety Guard 扩展到 RAG 主链路
   - Evaluation 接口访问限制

3. **建议在 `README.md` 和 `docs/api.md` 中添加安全声明**，说明当前 Demo 阶段的安全限制

---

## 附录：安全审查检查清单

| 检查项 | 状态 | 说明 |
|---|---|---|
| API Key 是否硬编码 | ✅ 通过 | 未发现硬编码 |
| `.env` 是否被 git 跟踪 | ✅ 通过 | `.gitignore` 已排除 |
| 日志是否打印 API Key | ✅ 通过 | 未发现 |
| 日志是否打印用户隐私 | ⚠️ 警告 | 打印完整用户问题 |
| CORS 是否过宽 | 🔴 失败 | `allowedOriginPatterns("*")` |
| 是否有认证授权 | 🔴 失败 | 无任何认证机制 |
| Python 服务是否可直接访问 | 🔴 失败 | 端口 8000 无访问控制 |
| Safety Guard 覆盖范围 | ⚠️ 警告 | 仅 Agent 链路 |
| Evaluation 接口是否受限 | 🔴 失败 | 任何用户可访问 |
| 异常信息是否暴露 | ⚠️ 警告 | 返回详细错误到前端 |
| traceId 是否可伪造 | ⚠️ 警告 | 前端生成的 traceId 被信任 |
| 请求大小是否有限制 | ⚠️ 警告 | 无限制 |
| RestTemplate 是否有超时 | ⚠️ 警告 | 无超时配置 |
| Prompt Injection 防护 | ⚠️ 警告 | 仅关键词匹配 |

---

**审查完成时间：** 2026-07-10
**审查人：** A4 安全审查 Agent
**分支：** audit/security-review
