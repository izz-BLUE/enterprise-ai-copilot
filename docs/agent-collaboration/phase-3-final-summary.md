# Phase 3 Final Summary

> Phase 3 质量加固阶段性收官。本文档记录完成内容、当前安全边界、未完成事项和面试展示口径。

---

## 1. 当前项目定位

Enterprise AI Copilot 是一个**本地 Demo / 面试演示项目**，定位为：

- 企业 AI 应用后端工程实践参考
- Java 后端转 AI 应用开发的 Demo
- RAG / Agent / Evaluation 工程链路实验项目

**已完成生产化准备改造**（Phase 3 共 12 项修复），但：

- **不做公网部署**，不配置服务器 / 域名 / Nginx
- **不能声明生产安全全部完成** — FIX-003（Python 服务裸露）仍是上线前阻塞项
- 当前方案是**最小 Admin Token + Evaluation 访问限制**，不是完整用户权限体系
- `admin.token` 为空时属于 Demo 便捷模式，不具备生产安全性

---

## 2. Phase 3 完成内容总览

### Batch 1：基础安全修复

| FIX | 内容 | Owner |
|-----|------|-------|
| FIX-010 | `.gitignore` 覆盖 `data/eval/reports/` | A1 |
| FIX-011 | `.gitignore` 覆盖 `node_modules/` | A1 |
| FIX-012 | `AgentHealthController` 地址配置化（`python.agent.base-url`） | A1 |
| FIX-004 | 普通 RAG 链路 Safety Guard 前置检查 | A2 |

### Batch 2：稳定性与边界收敛

| FIX | 内容 | Owner |
|-----|------|-------|
| FIX-001 | CORS 从 `*` 收敛为可配置白名单（`cors.allowed-origins`） | A1 |
| FIX-013 | Java 调 Python RestTemplate 超时（连接 3s / 读取 30s） | A1 |
| FIX-014 | Python / LangGraph LLM 调用超时（`LLM_TIMEOUT` 默认 30s） | A2 |
| FIX-017 | Java `@Size(max=2000)` + Python `MAX_MESSAGE_LENGTH` 双层输入校验 | A1 + A2 |

### Batch 3-A：低风险安全收敛

| FIX | 内容 | Owner |
|-----|------|-------|
| FIX-016 | traceId 由 Java 服务端统一生成，不信任客户端 `X-Trace-Id` | A1 |
| FIX-015 | Java / Python 异常信息收敛，`reason` 字段不暴露 `e.getMessage()` / `str(e)` | A1 + A2 |

### Batch 3-B：权限与 Evaluation 访问限制

| FIX | 内容 | Owner |
|-----|------|-------|
| FIX-002 | `admin.token` 最小权限控制 + `X-Admin-Token` 校验 | A1 |
| FIX-005 | Java 传递 `X-Allow-Eval` + Python `router_node` 受控 eval 路由 | A1 + A2 |

### A5：部署准备

| 内容 | 说明 |
|------|------|
| `docs/deployment-readiness.md` | 部署准备文档（环境变量、启动流程、检查清单） |
| `agent-python/.env.example` | Python 环境变量模板 |
| `start-local.ps1` | 本地一键启动脚本 |
| `health-check.ps1` | 三端健康检查脚本 |

### A3 / A4 复验

| 报告 | 结论 |
|------|------|
| QA 回归复验 | Batch 1~3-B 共 12 项修复全部验证通过，未引入回归 |
| 安全复验 | 12 项修复有效；FIX-003 仍为 P0 阻塞项；可继续本地 Demo，不可声明生产安全完成 |

---

## 3. 当前安全边界

### 权限模型

| 角色 | 能力 | 说明 |
|------|------|------|
| 普通用户 | RAG 问答、Safety Guard 拒答 | 不需要 Token |
| 管理员 | 普通用户能力 + Evaluation 查询 | 需要 `X-Admin-Token` 匹配 `admin.token` |

### 关键约束

| 约束 | 说明 |
|------|------|
| 前端 role 不可信 | 权限判断必须在 Java 后端完成 |
| Java 是权限判断入口 | Python 仅根据 Java 传递的 header 行动 |
| `X-Allow-Eval` 不是认证凭证 | 只是 Java → Python 内部控制信号 |
| `admin.token` 为空 = Demo 模式 | 所有功能可用，不具备生产安全性 |
| Python 服务裸露 | 端口 8000 可被直接访问，攻击者可伪造 `X-Allow-Eval`（FIX-003） |

### 安全覆盖矩阵

| 链路 | Safety Guard | 输入校验 | 超时控制 | 权限控制 |
|------|-------------|---------|---------|---------|
| RAG 主链路 `/api/chat` | ✅ | ✅ Java + Python | ✅ | N/A（公开） |
| Agent 链路 `/api/agent/langgraph/chat` | ✅ | ✅ Java + Python | ✅ | ✅ Eval 受限 |
| Python 直接访问 `:8000` | ✅ | ✅ Python | ✅ | ❌ 无控制 |

---

## 4. 当前仍未完成事项

### P0：上线前必须完成

| 项目 | 说明 |
|------|------|
| **FIX-003：Python 服务裸露** | 端口 8000 无访问控制，可绕过 Java 层所有检查。需要部署拓扑 / 内网绑定 / 防火墙 / 反向代理解决 |
| **正式认证体系** | 当前 Admin Token 是共享密钥，无 per-user 身份。上线需 JWT + 用户体系 |

### P1：进入开发修复前必须处理

| 项目 | 说明 |
|------|------|
| FIX-018：sources 字段内部文件名脱敏 | 暴露知识库结构和文件命名规则 |
| FIX-021：日志打印完整用户问题 | 生产环境可能泄露用户隐私 |
| FIX-024：无请求频率限制 | 可被暴力调用 |

### P2：后续优化

| 项目 | 说明 |
|------|------|
| FIX-020：Safety Guard 变体绕过 | 仅关键词匹配，无法防同音字 / 拼音 / 英文 |
| FIX-022：两个 RAG Prompt 模板不一致 | 主链路和 Agent 链路 Prompt 规则差异 |
| FIX-023：Prompt Injection 防护 | 缺少"禁止忽略系统提示词"等规则 |
| FIX-025：日志格式未区分环境 | 生产环境需要 JSON 格式日志 |
| Docker Compose 本地生产模拟 | 一键启动三端 + 网络隔离 |
| 前端管理台 | 知识库管理、用户管理、评估管理 |

---

## 5. 面试演示启动流程

### 前置准备

```powershell
# 1. 启动 Python Agent
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 2. 启动 Java Backend（新终端）
cd backend-java
.\mvnw.cmd spring-boot:run

# 3. 启动 Frontend（新终端）
cd frontend
npm install
npm run dev

# 4. 执行健康检查（新终端）
.\health-check.ps1
```

### 演示步骤

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 普通 RAG 问答：`病假需要提供哪些材料？` | `route=rag`, `success=true`, answer 包含病假材料 |
| 2 | Safety Guard 拒答：`怎么伪造病假证明？` | `route=refuse`, `safe=false`, category=`illegal_or_policy_violation` |
| 3 | Eval 查询（无 Token）：`当前RAG评估通过率是多少？` | `route=refuse`, category=`access_control`（admin.token 非空时） |
| 4 | Eval 查询（带 Token）：同上 + `X-Admin-Token: your-token` | `route=eval`, 返回评估报告摘要 |
| 5 | 展示 traceId 全链路 | 响应体和响应头都有 traceId，日志中可追踪 |
| 6 | 展示部署准备文档 | `docs/deployment-readiness.md` |

---

## 6. 面试讲解口径

### 项目介绍（30 秒）

> 这是一个企业知识库 AI 应用后端项目，Java Spring Boot 做业务网关，Python FastAPI 做 AI 服务引擎，React 做前端演示。支持 RAG 问答、LangGraph Agent 实验链路和两层评估体系。

### 生产化加固（1 分钟）

> 我不是简单做了一个 RAG Demo，而是按生产化风险做了多轮安全加固。Phase 3 一共做了 12 项修复，覆盖 CORS 收紧、超时控制、输入校验、Safety Guard、traceId 链路追踪、异常信息收敛、Admin Token 权限边界、Evaluation 访问限制。

### 权限设计（30 秒）

> 我把 Evaluation 定位为管理员诊断能力，而不是普通用户功能。用最小 Admin Token 方案做了权限边界 — Java 后端校验 `X-Admin-Token`，通过 `X-Allow-Eval` header 告知 Python 是否允许 eval 路由。不引入复杂登录系统，但把权限判断集中在 Java 后端，Python 不信任任何外部直接调用。

### 诚实边界（30 秒）

> Python 服务裸露我没有假装解决，而是明确列为部署前阻塞项（FIX-003）。当前项目不做公网部署，但已完成部署准备文档、环境变量模板、一键启动脚本和健康检查脚本。如果要上线，第一步是通过部署拓扑解决 Python 服务访问控制。

---

## 7. 可展示亮点

| 亮点 | 说明 |
|------|------|
| **Java + Python 双服务架构** | Java 做业务网关，Python 做 AI 引擎，职责清晰 |
| **RAG 主链路** | 手写全链路，不依赖 LangChain，Hybrid Retrieval（Faiss + BM25 + RRF） |
| **LangGraph Agent 实验链路** | Safety Guard → 意图路由 → RAG / Eval / Refuse 多分支 |
| **Safety Guard** | 5 类风险关键词匹配，覆盖 RAG + Agent 两条链路 |
| **Evaluation 回归评估** | 38 cases，两层评估（Retrieval + Generation），flaky 检测，baseline 回归 |
| **traceId 全链路追踪** | Java 服务端统一生成 → Python 透传 → 日志关联 |
| **timeout / fallback** | Java RestTemplate 超时 + Python LLM 超时 + 异常兜底 |
| **CORS 白名单** | 从 `*` 收敛为可配置白名单 |
| **Admin Token 权限边界** | 最小方案保护 Evaluation，不引入复杂登录 |
| **部署准备** | 完整环境变量清单、启动脚本、健康检查、部署前检查清单 |
| **多 Agent 协作开发流程** | 9 个协作文档、任务看板、Session 注册、分支管理、合并检查清单 |

---

## 8. 后续可选增强

| 增强 | 说明 | 优先级 |
|------|------|--------|
| Docker Compose 本地生产模拟 | 一键启动三端 + 网络隔离 Python | P1 |
| Python 服务仅内网访问 | 部署拓扑 / 防火墙 / 反向代理 | P0 |
| 正式登录 / JWT / RBAC | 替换 Admin Token，per-user 身份 | P0 |
| sources 脱敏 | 文件名映射为文档标题 | P1 |
| 日志脱敏 | 用户问题截断，区分 debug/prod | P1 |
| 限流 | API 频率限制 | P2 |
| Prompt Injection 防护 | 补充防注入规则 | P2 |
| 前端管理台 | 知识库管理、用户管理、评估管理 | P2 |
| CI/CD 集成 | GitHub Actions 自动 eval + 部署 | P2 |
