# Deployment Readiness Guide

> **文档版本：** 2026-07-10
> **文档目的：** 记录项目当前部署准备状态、本地生产模拟方案、环境变量说明和未来上线前必须完成事项。

---

## 1. 当前部署策略

| 项目 | 说明 |
|---|---|
| 项目定位 | 本地 Demo / 面试演示项目 |
| 当前部署方式 | 本地三端启动（Python + Java + Frontend） |
| 是否已部署公网 | **否** |
| 是否配置服务器 | **否** |
| 是否配置域名 / Nginx | **否** |

**已完成的生产化准备：**

- CORS 白名单可配置（FIX-001）
- 最小 Admin Token + Evaluation 访问限制（FIX-002）
- Safety Guard 覆盖 RAG 主链路（FIX-004）
- Evaluation 路由受控（FIX-005）
- RestTemplate 超时配置（FIX-013）
- LLM 调用超时配置（FIX-014）
- 异常信息收敛（FIX-015）
- traceId 统一生成与验证（FIX-016）
- 输入长度校验（FIX-017）

**尚未完成的生产化项：**

- Python 服务访问控制（FIX-003）— 部署准备说明已完成，实际实施需上线时处理
- sources 脱敏（FIX-018）
- 日志脱敏（FIX-021）
- RAG Prompt Injection 防护（FIX-023）
- 请求频率限制（FIX-024）
- 用户认证体系
- 审计日志
- Docker Compose 编排
- CI/CD 流水线

---

## 2. 不做公网部署声明

**本阶段明确不做以下事项：**

- 不做公网部署
- 不配置服务器
- 不配置域名
- 不配置 Nginx
- 不修改业务逻辑
- 不修改 RAG / Agent / Evaluation 逻辑
- 不引入复杂 Docker 编排
- 不声明生产部署完成
- 不声明生产安全完成

**本阶段目标：** 部署准备与本地生产模拟。

---

## 3. 本地生产模拟架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        本地开发环境                               │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Frontend   │    │ Java Backend │    │ Python Agent │      │
│  │  :5173       │───▶│  :8080       │───▶│  :8000       │      │
│  │  React+Vite  │    │  Spring Boot │    │  FastAPI     │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                    │                    │             │
│         │              ┌─────┴─────┐              │             │
│         │              │ Admin     │              │             │
│         │              │ Token     │              │             │
│         │              │ 权限校验   │              │             │
│         │              └───────────┘              │             │
│         │                                         │             │
│         └─────────────────────────────────────────┘             │
│                    仅本地访问 (127.0.0.1)                        │
└─────────────────────────────────────────────────────────────────┘
```

**架构要点：**

| 层 | 端口 | 职责 | 访问范围 |
|---|---|---|---|
| Frontend | 5173 | 用户交互界面 | 本地浏览器 |
| Java Backend | 8080 | 统一 API 入口、权限校验、请求转发 | 本地 + Frontend |
| Python Agent | 8000 | RAG 检索、LLM 调用、Agent 编排 | **仅 Java 后端访问** |

**安全边界：**

- Java 后端是唯一对外 API 入口
- Java 负责 traceId 生成、CORS、输入校验、Admin Token 权限判断
- Python 服务不应直接暴露给外部访问
- 当前本地运行时，Python 仅监听 localhost，外部无法直接访问

---

## 4. 环境变量清单

### 4.1 Python 环境变量（agent-python/.env）

| 变量名 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | **是** | — | DeepSeek API Key，LLM 调用必需 |
| `DEEPSEEK_BASE_URL` | 否 | — | DeepSeek API 地址，默认使用 SDK 默认地址 |
| `DEEPSEEK_MODEL` | 否 | — | 模型名称，默认使用 SDK 默认模型 |
| `DEEPSEEK_TEMPERATURE` | 否 | `0` | LLM 温度参数 |
| `LLM_TIMEOUT` | 否 | `30` | LLM 调用超时（秒） |
| `AI_MAX_CONCURRENT_REQUESTS` | 否 | `3` | Python AI 请求并发槽 |
| `AI_QUEUE_TIMEOUT_MS` | 否 | `500` | Python 获取并发槽最长等待时间（毫秒） |
| `MAX_MESSAGE_LENGTH` | 否 | `2000` | 输入消息最大长度（字符） |
| `REWRITE_MODE` | 否 | `none` | 查询重写模式：`none` / `rule`（实验） |
| `RERANK_MODEL` | 否 | `BAAI/bge-reranker-base` | Cross Encoder 精排模型（实验） |
| `RERANK_CANDIDATE_K` | 否 | `10` | 精排候选数量（实验） |
| `HF_HUB_OFFLINE` | 否 | — | 设为 `1` 启用 HuggingFace 离线模式（国内网络必须） |

**注意：** 代码中使用的环境变量名是 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。README.md 中记录的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 与代码不一致，应以代码为准。

### 4.2 Java 配置（backend-java/src/main/resources/application.properties）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `python.agent.base-url` | `http://localhost:8000` | Python 服务地址 |
| `python.agent.connect-timeout` | `3000` | Java → Python 连接超时（毫秒） |
| `python.agent.read-timeout` | `40000` | Java → Python 读取超时（毫秒） |
| `python.agent.max-concurrent-requests` | `3` | Java → Python 在途 AI 请求上限 |
| `python.agent.acquire-timeout-ms` | `500` | Java 获取并发槽最长等待时间（毫秒） |
| `cors.allowed-origins` | `http://localhost:5173,http://127.0.0.1:5173` | CORS 允许的来源（逗号分隔） |
| `admin.token` | （空） | Admin Token，空 = Demo 模式，非空 = 需校验 |
| `logging.pattern.console` | （含 traceId） | 日志格式 |

**Java 配置覆盖方式：** 可通过环境变量覆盖，如 `ADMIN_TOKEN=xxx` 覆盖 `admin.token`。

### 4.3 Frontend 配置

Frontend 无独立环境变量配置。API 请求地址通过 Vite 代理或直接请求 Java 后端 `http://localhost:8080`。

### 4.4 文档与代码配置名不一致说明

| 文档中记录 | 代码实际使用 | 说明 |
|---|---|---|
| `LLM_API_KEY` | `DEEPSEEK_API_KEY` | 以代码为准 |
| `LLM_BASE_URL` | `DEEPSEEK_BASE_URL` | 以代码为准 |
| `LLM_MODEL` | `DEEPSEEK_MODEL` | 以代码为准 |

> 本次仅修正文档说明，不修改业务代码。

---

## 5. 本地启动流程

### 前置条件

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.11+ | AI 服务运行环境 |
| Java | 17+ | 后端服务运行环境 |
| Node.js | 18+ | 前端构建环境 |
| uv | 最新版 | Python 包管理器 |
| Maven | 3.8+（或项目内 mvnw） | Java 构建工具 |

### 启动顺序

**必须按以下顺序启动：** Python → Java → Frontend

#### Step 1: 启动 Python AI Service（端口 8000）

```bash
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

**验证：**
```bash
curl http://localhost:8000/agent/health
# 预期：{"service": "agent-python", "status": "UP"}
```

#### Step 2: 启动 Java Backend（端口 8080）

```bash
cd backend-java
./mvnw spring-boot:run
```

Windows PowerShell：
```powershell
cd backend-java
.\mvnw.cmd spring-boot:run
```

**验证：**
```bash
curl http://localhost:8080/api/health
# 预期：{"service": "backend-java", "status": "UP"}
```

#### Step 3: 启动 Frontend（端口 5173）

```bash
cd frontend
npm install
npm run dev
```

**验证：** 浏览器访问 http://localhost:5173

#### Step 4: 健康检查

运行健康检查脚本（Windows PowerShell）：
```powershell
.\health-check.ps1
```

或手动执行：
```bash
# Python 健康检查
curl http://localhost:8000/agent/health

# Java 健康检查
curl http://localhost:8080/api/health

# Python 通过 Java 代理健康检查
curl http://localhost:8080/api/agent/health
```

#### Step 5: RAG 问答验证

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
# 预期：success=true，answer 包含病假材料清单
```

#### Step 6: Agent / Eval 权限验证

```bash
# Agent RAG 问答
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
# 预期：route=rag, safe=true

# Eval 查询（Demo 模式应可用）
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=eval, 返回评估报告
```

### 一键启动脚本

项目提供 Windows PowerShell 启动脚本：

```powershell
.\start-local.ps1
```

该脚本会按顺序启动三个服务，并在每个服务启动后执行健康检查。

---

## 6. 健康检查与验证命令

### 6.1 服务健康检查

| 检查项 | 命令 | 预期响应 |
|---|---|---|
| Python 服务 | `curl http://localhost:8000/agent/health` | `{"service":"agent-python","status":"UP"}` |
| Java 服务 | `curl http://localhost:8080/api/health` | `{"service":"backend-java","status":"UP"}` |
| Python 通过 Java | `curl http://localhost:8080/api/agent/health` | `{"service":"agent-python","status":"UP"}` |

### 6.2 功能验证

| 检查项 | 命令 | 预期 |
|---|---|---|
| 普通 RAG 问答 | `POST /api/chat` | `success=true`，answer 有内容 |
| Agent RAG 问答 | `POST /api/agent/langgraph/chat` | `route=rag`, `safe=true` |
| Eval 查询（Demo） | `POST /api/agent/langgraph/chat` + eval 问题 | `route=eval` |
| Safety Guard 拦截 | `POST /api/chat` + 高风险问题 | `success=true`，安全拒答文案 |
| 超长输入拦截 | `POST /api/chat` + 2001 字符 | `success=false`，"输入内容过长" |

### 6.3 curl 命令集

```bash
# === 健康检查 ===
curl http://localhost:8000/agent/health
curl http://localhost:8080/api/health
curl http://localhost:8080/api/agent/health

# === RAG 问答 ===
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'

# === Agent RAG 问答 ===
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'

# === Eval 查询 ===
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'

# === Safety Guard ===
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么伪造病假证明？"}'

# === Python 停服降级 ===
# 先停止 Python 服务，再调用 Java
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"测试"}'
# 预期：success=false，"当前 AI 服务暂时不可用"
```

---

## 7. Admin Token / Evaluation 验证

### 7.1 Demo 模式（admin.token 为空，默认）

- 所有功能可用，包括 Evaluation 查询
- 不需要配置任何 Token
- 适合本地开发和面试演示

**验证：**
```bash
# Eval 查询应直接可用
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=eval，返回评估报告
```

### 7.2 Admin Token 模式（admin.token 非空）

- 普通 RAG 问答不受影响
- Evaluation 查询需要 `X-Admin-Token` 请求头匹配
- 无权限时返回 `route=refuse, category=access_control`

**设置方式：**
```properties
# application.properties
admin.token=your-secret-token
```

或通过环境变量：
```bash
ADMIN_TOKEN=your-secret-token
```

**验证：**
```bash
# 无 Token → 被拒绝
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=refuse, category=access_control

# 正确 Token → 允许
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: your-secret-token" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=eval，返回评估报告

# 错误 Token → 被拒绝
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: wrong-token" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：route=refuse, category=access_control
```

### 7.3 权限链路说明

```
用户请求 → Java LangGraphAgentController
  → 检查 admin.token / X-Admin-Token
  → 设置 X-Allow-Eval: true/false
  → Python /agent/langgraph/chat
    → router_node 根据 allow_eval 控制
    → allow_eval=false + eval 关键词 → route=refuse (access_control)
    → allow_eval=true + eval 关键词 → route=eval
```

**安全边界声明：**

- `X-Allow-Eval` 是 Java → Python 的内部传递信号，**不是认证凭证**
- 权限判断在 Java 后端完成，Python 不应将其当作独立安全边界
- 当前方案是**最小 Admin Token + Evaluation 访问限制**，不是完整用户权限体系

---

## 8. Python 服务边界说明（FIX-003）

### 8.1 当前状态

**FIX-003 是当前唯一剩余的 P0 生产化阻塞项。**

- Python FastAPI 服务（端口 8000）无独立访问控制
- 直接访问 Python 服务可绕过 Java 层所有安全检查
- 攻击者可伪造 `X-Allow-Eval: true` 直接访问 eval 能力

### 8.2 本地 Demo 风险评估

**当前不公网部署，Python 服务裸露不阻塞本地 Demo：**

- 本地运行时 Python 仅监听 `127.0.0.1`（localhost）
- 外部网络无法直接访问本地端口
- 面试演示场景下风险可控

### 8.3 未来部署必须处理

**如果未来部署到公网或服务器，Python 服务不得直接暴露公网。** 推荐方案：

| 方案 | 说明 | 优先级 |
|---|---|---|
| Python 仅监听 127.0.0.1 | 绑定 localhost，不允许外部直接访问 | **推荐** |
| 防火墙禁止外部访问 8000 | iptables / 云安全组规则 | **推荐** |
| Docker Compose 不映射公网端口 | Python 容器端口仅内部网络可访问 | **推荐** |
| 反向代理只暴露 Java / Frontend | Nginx 仅代理 8080 和 5173 | **推荐** |
| Python 添加内部 API Key | 应用层兜底，Java 调用时携带 | 可选 |

### 8.4 推荐部署架构（未来）

```
                    ┌─────────────────────────────────┐
                    │         公网 / 外部访问           │
                    └──────────┬──────────────────────┘
                               │
                    ┌──────────▼──────────────────────┐
                    │     反向代理 (Nginx)              │
                    │     :80 / :443                   │
                    │     仅暴露 Java + Frontend       │
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼────────┐ ┌────▼────┐  ┌────────▼────────┐
     │   Frontend      │ │  Java   │  │   Python Agent   │
     │   :5173         │ │  :8080  │  │   :8000          │
     │   静态文件       │ │  API    │  │   仅内部访问      │
     └─────────────────┘ └─────────┘  └──────────────────┘
                                       不映射公网端口
```

### 8.5 当前阶段处理

**当前阶段只做部署准备说明，不实际实施上线部署。** FIX-003 标记为：

> **部署前阻塞项，已完成部署准备说明。** 上线前必须实施 Python 服务访问控制。

---

## 9. 部署前检查清单

如果未来要部署到公网或服务器，必须逐项确认：

### 9.1 环境配置

- [ ] `DEEPSEEK_API_KEY` 已配置且有效
- [ ] `admin.token` 已设置为非空值
- [ ] `cors.allowed-origins` 已配置为实际域名
- [ ] `python.agent.base-url` 已配置为内网地址
- [ ] `HF_HUB_OFFLINE=1` 已设置（如需离线模式）

### 9.2 安全加固

- [ ] Python 服务仅监听 127.0.0.1 或内网地址
- [ ] 防火墙禁止外部访问 8000 端口
- [ ] 反向代理仅暴露 Java (8080) 和 Frontend (5173)
- [ ] `admin.token` 已配置为强随机值
- [ ] CORS 白名单仅包含实际域名
- [ ] 已检查 `.env` 未被提交到 Git

### 9.3 服务验证

- [ ] Python 健康检查通过
- [ ] Java 健康检查通过
- [ ] Frontend 页面可访问
- [ ] RAG 问答正常返回
- [ ] Agent 问答正常返回
- [ ] Eval 查询（带 Token）正常返回
- [ ] Safety Guard 拦截正常
- [ ] Python 停服降级正常

### 9.4 知识库

- [ ] 知识库文档已准备
- [ ] chunks.json 已构建
- [ ] faiss.index 已构建
- [ ] 评估用例已准备
- [ ] 评估 baseline 已更新

### 9.5 监控与日志

- [ ] 日志格式已配置（生产环境建议 JSON 格式）
- [ ] 日志级别已调整（生产环境建议 WARN 以上）
- [ ] 异常监控已配置（如有）
- [ ] LLM 调用监控已配置（如有）

---

## 10. 未来上线前必须完成事项

### P0：上线阻塞项

| 事项 | 说明 | 优先级 |
|---|---|---|
| FIX-003：Python 服务访问控制 | Python 不得直接暴露公网 | **必须** |
| admin.token 配置 | 设置为强随机值，或替换为正式认证体系 | **必须** |
| CORS 收紧 | 仅允许实际域名 | **必须** |
| .env 安全 | 确认 .env 未被提交，API Key 安全存储 | **必须** |

### P1：上线前建议完成

| 事项 | 说明 | 优先级 |
|---|---|---|
| FIX-018：sources 脱敏 | 不暴露内部文件名 | 建议 |
| FIX-021：日志脱敏 | 日志不打印完整用户问题 | 建议 |
| FIX-023：Prompt Injection 防护 | RAG Prompt 添加防护规则 | 建议 |
| FIX-024：请求频率限制 | 防止滥用 | 建议 |
| Docker Compose 编排 | 一键部署 | 建议 |
| CI/CD 流水线 | 自动化测试和部署 | 建议 |

### P2：上线后持续优化

| 事项 | 说明 | 优先级 |
|---|---|---|
| 用户认证体系 | 替换 Admin Token 为 JWT + 用户体系 | 后续 |
| 审计日志 | 记录所有请求和操作 | 后续 |
| 监控告警 | 服务健康监控、异常告警 | 后续 |
| 多租户隔离 | 不同租户数据隔离 | 后续 |
| 文档上传管理 | 知识库文档在线管理 | 后续 |

---

## 附录 A：文件清单

| 文件 | 用途 |
|---|---|
| `docs/deployment-readiness.md` | 本文件，部署准备主文档 |
| `agent-python/.env.example` | Python 环境变量示例 |
| `start-local.ps1` | Windows 本地一键启动脚本 |
| `health-check.ps1` | Windows 健康检查脚本 |

## 附录 B：相关文档

| 文档 | 路径 | 说明 |
|---|---|---|
| 项目 README | `README.md` | 项目全貌 |
| 架构说明 | `docs/architecture.md` | 架构图、模块说明 |
| 接口文档 | `docs/api.md` | API 接口文档 |
| 本地演示指南 | `docs/local-demo-guide.md` | 本地启动和演示 |
| Phase 3 修复计划 | `docs/agent-collaboration/phase-3-remediation-plan.md` | 修复计划和状态 |
| 协作仪表盘 | `docs/agent-collaboration/dashboard.md` | 项目总控视图 |
