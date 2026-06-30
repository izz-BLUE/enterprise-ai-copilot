# 本地演示指南（Local Demo Guide）

## 项目演示定位

Enterprise AI Copilot 当前为**本地可复现的 RAG 应用后端 Demo**，尚未部署到公网服务器。

本文档说明如何在本地完整启动 Java / Python / Frontend 三端服务，用于面试演示或本地体验。

## 前置条件

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11+ | AI 服务运行环境 |
| Java | 17+ | 后端服务运行环境 |
| Node.js | 18+ | 前端构建环境 |
| uv | 最新版 | Python 包管理器 |
| Maven | 3.8+（或项目内 mvnw） | Java 构建工具 |

**可选依赖：**

| 依赖 | 说明 |
|------|------|
| Git | 版本管理 |
| curl 或浏览器 | 健康检查 |

## 环境变量配置

在 `agent-python/` 目录下创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

**可选配置：**

```env
# 检索模式：vector（默认）/ hybrid / hybrid_rerank（实验模式）
RETRIEVAL_MODE=vector

# 查询重写：none（默认）/ rule（实验模式）
REWRITE_MODE=none
```

**注意：**

- 不要将 `.env` 提交到 Git
- 不要在前端代码或文档中暴露 API Key
- `hybrid_rerank` 和 `rewrite_mode=rule` 是实验模式，不建议默认启用

## 启动服务

需要启动 3 个服务，建议按以下顺序操作。

### 1. Python AI Service（端口 8000）

```bash
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 2. Java Backend（端口 8080）

```bash
cd backend-java
./mvnw spring-boot:run
```

Windows PowerShell：

```powershell
cd backend-java
.\mvnw.cmd spring-boot:run
```

### 3. Frontend（端口 5173）

```bash
cd frontend
npm install
npm run dev
```

## 服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| Frontend | http://localhost:5173 | 浏览器访问 |
| Java Backend | http://localhost:8080 | API 入口 |
| Python AI Service | http://localhost:8000 | AI 服务引擎 |

## 健康检查

### Python AI Service

```bash
curl http://localhost:8000/agent/health
```

预期响应：`{"status": "ok", "agent_ready": true}`

### Java Backend

```bash
curl http://localhost:8080/api/health
```

预期响应：包含 `status: "ok"` 的 JSON

### Python Agent 通过 Java 代理

```bash
curl http://localhost:8080/api/agent/health
```

预期响应：`{"status": "ok", "agent_ready": true}`

## RAG 问答测试

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'
```

预期响应：包含回答文本和 sources 列表的 JSON

## 常见问题排查

### Python 启动失败

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| `ModuleNotFoundError` | 依赖未安装 | `uv sync` |
| `DEEPSEEK_API_KEY 未配置` | .env 缺失或格式错误 | 检查 `.env` 文件 |
| `faiss.index not found` | 索引未构建 | 运行 `build_faiss_index.py` |

### Java 启动失败

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| `Connection refused` | Python 服务未启动 | 先启动 Python 服务 |
| `port 8080 already in use` | 端口被占用 | 关闭占用进程或修改端口 |

### Frontend 启动失败

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| `npm install` 报错 | Node.js 版本过低 | 升级到 Node.js 18+ |
| 页面无法加载 | Java 服务未启动 | 先启动 Java 服务 |

### LLM 调用失败

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| `401 Unauthorized` | API Key 无效 | 检查 `.env` 中的 Key |
| `timeout` | 网络问题或 API 过载 | 检查网络连接 |
| `模型不存在` | 模型名称错误 | 检查 `DEEPSEEK_MODEL` |

### Retrieval 评估可独立运行

即使没有 API Key，retrieval 评估仍然可以运行（不调用 LLM）：

```bash
cd agent-python
uv run python scripts/eval/run_rag_eval.py
```

Generation 评估需要 API Key（会调用 LLM）。

## 面试前检查清单

演示前 15 分钟，逐项确认：

- [ ] Python 服务运行正常（`/agent/health` 返回 ok）
- [ ] Java 服务运行正常（`/api/health` 返回 ok）
- [ ] Frontend 页面可访问（http://localhost:5173）
- [ ] RAG 问答可正常返回结果
- [ ] API Key 有效且有余额
- [ ] 网络连通（可访问 DeepSeek API）
- [ ] 准备好截图 / 录屏作为兜底方案

**如果现场服务失败，切换到截图 / 录屏演示（见 `demo-script.md`）。**
