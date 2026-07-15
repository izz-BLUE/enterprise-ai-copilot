# Demo 脚本

> 10 分钟面试 Demo 路线。按顺序执行，每步有操作、预期和话术。

---

## Demo 前准备

### 启动 Python Agent

```powershell
cd agent-python
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 启动 Java Backend

```powershell
cd backend-java
.\mvnw.cmd spring-boot:run
```

### 启动 Frontend

```powershell
cd frontend
npm install
npm run dev
```

### 执行健康检查

```powershell
.\health-check.ps1
```

**预期：** 三端均返回 `UP` 状态。

**如果 health-check 失败：** 检查三个终端是否都在运行，Python 是否在 8000 端口，Java 是否在 8080 端口。

---

## Demo 路线

### 1. 健康检查（30 秒）

**演示目标：** 确认三端服务正常。

**操作：**
```powershell
.\health-check.ps1
```

**预期结果：**
```
Python Agent: UP (http://localhost:8000/agent/health)
Java Backend: UP (http://localhost:8080/api/health)
Python via Java: UP (http://localhost:8080/api/agent/health)
Frontend: UP (http://localhost:5173)
```

**话术：**
> 这是三端健康检查脚本。Python Agent 在 8000 端口，Java Backend 在 8080 端口，前端在 5173 端口。`/api/agent/health` 是 Java 代理 Python 的健康检查，验证 Java → Python 链路通畅。

**如果失败：** 停在健康检查这步排查，不要继续演示。

---

### 2. 普通 RAG 问答（1 分钟）

**演示目标：** 展示 RAG 主链路能力。

**操作：** 在前端输入 `病假需要提供哪些材料？`，选择 `普通 RAG` 模式。

**预期结果：**
- `success: true`
- `model: deepseek-chat`
- answer 包含病假材料相关内容
- traceId 展示

**话术：**
> 这是 RAG 主链路。用户问题进来后，Java 做输入校验和 traceId 生成，转发给 Python。Python 先过 Safety Guard，然后用 Faiss 语义检索 + BM25 关键词检索 + RRF 融合排序，取 TopK=3 的 chunk 拄给 LLM 生成回答。

**如果失败：** 检查 Python 服务是否运行，`.env` 中 API Key 是否配置。

---

### 3. 无答案拒答（30 秒）

**演示目标：** 展示知识库无命中时的拒答能力。

**操作：** 输入 `公司的股票代码是什么？`

**预期结果：**
- `success: true`
- answer 包含"当前知识库暂无相关信息"或类似拒答文案

**话术：**
> 知识库里没有股票相关信息，RAG 会诚实拒答而不是编造答案。这是通过 Prompt 规则控制的 — "当前知识库暂无相关信息，不要编造"。

---

### 4. Safety Guard 高风险问题拒答（1 分钟）

**演示目标：** 展示 Safety Guard 输入安全检查。

**操作：** 切换到 `LangGraph Agent` 模式，输入 `怎么伪造病假证明？`

**预期结果：**
- `route: refuse`
- `safe: false`
- `category: illegal_or_policy_violation`
- answer 为安全拒答文案

**话术：**
> Safety Guard 在检索之前做前置检查，检测到"伪造"这个高风险关键词，直接拒答不进入检索和 LLM。这是规则版基础防护，覆盖 5 类风险：违法违规、绕过制度、网络安全攻击、删除审计、越权访问。两条链路都会经过 Safety Guard。

**如果没触发拒答：** 检查 `safety_guard.py` 是否包含"伪造"关键词。

---

### 5. LangGraph Agent 普通问答（30 秒）

**演示目标：** 展示 Agent 链路的 RAG 能力。

**操作：** 输入 `病假需要提供哪些材料？`

**预期结果：**
- `route: rag`
- `safe: true`
- `sources` 有值（chunk ID 列表）

**话术：**
> Agent 链路和 RAG 主链路的区别是：Agent 有 Safety Guard + 意图路由 + Tool Calling，而且返回 sources 引用来源。普通问题走 rag 路由，评估问题走 eval 路由，高风险问题走 refuse 路由。

---

### 6. Admin Token 模式下 Evaluation 访问限制（1.5 分钟）

**演示目标：** 展示 Evaluation 权限控制。

**前置条件：** `application.properties` 中 `admin.token` 已配置为非空值（如 `admin.token=demo-secret`）。

**操作：** 输入 `当前RAG评估通过率是多少？`（不带 X-Admin-Token）

**预期结果：**
- `route: refuse`
- `category: access_control`
- `success: true`
- answer: "该问题涉及内部评估诊断能力，仅管理员可访问。"

**话术：**
> Evaluation 是管理员诊断能力，不是普通用户功能。`admin.token` 非空时，Java 后端校验请求头 `X-Admin-Token`，不匹配就拒绝 eval 路由。权限判断在 Java 后端完成，不信任前端传来的 role。Python 侧通过 `X-Allow-Eval` header 接收 Java 的判断结果。

---

### 7. 正确 Admin Token 访问 Evaluation（1 分钟）

**演示目标：** 展示管理员如何访问 Evaluation。

**操作：** 用 curl 或 Postman 发送请求：
```bash
# 从本地环境变量读取；请预先通过安全方式设置 ADMIN_TOKEN，不要将真实值写入文档或命令历史。
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
```

**预期结果：**
- `route: eval`
- `safe: true`
- answer 包含评估指标（检索评估通过率、生成评估通过率等）

**话术：**
> 带正确的 `X-Admin-Token`，Java 判断为管理员，设置 `X-Allow-Eval: true` 传给 Python，eval 路由正常执行。Evaluation 读取本地 JSON 报告文件返回评估摘要，不调用 LLM。

**如果失败：** 检查 `admin.token` 配置值和请求头是否一致。

---

### 8. 展示 traceId（1 分钟）

**演示目标：** 展示 traceId 全链路追踪。

**操作：** 发送任意请求，观察响应头和响应体中的 traceId。

**预期结果：**
- 响应体包含 `traceId` 字段（UUID 格式）
- 响应头包含 `X-Trace-Id`
- Java 日志中每行都有 `[traceId]`
- 前端展示 traceId 标签

**话术：**
> traceId 由 Java 入口统一生成，格式是 UUID v4。客户端传入的 `X-Trace-Id` 不被信任，非法格式会丢弃重新生成。Java 通过 `X-Trace-Id` header 透传给 Python，两端日志都带 traceId，方便全链路排查。用户反馈问题时只需要提供 traceId，服务端通过日志就能定位。

---

### 9. 展示部署与质量文档（1 分钟）

**演示目标：** 展示部署边界和验证方法。

**操作：** 打开 `docs/deployment.md` 和 `docs/quality-assurance.md`，快速浏览目录。

**重点展示：**
- Docker Compose 网络与端口边界
- Nginx、HTTPS 和证书续签
- CI 与 Retrieval Evaluation
- 分层压测和发布后 Smoke 检查

**话术：**
> 公网部署通过 Nginx 统一入口，Java 只绑定 localhost，Python 只在 Docker 内网暴露。文档同时记录了 CI、检索评估和分层压测口径；这些验证支持当前部署结论，但不等于生产 SLA。

---

## Demo 后总结话术（30 秒）

> 总结一下：RAG 使用 FAISS + BM25 + RRF 融合检索，实验性 Agent 使用 LangGraph 状态图，检索质量通过 38 个固定用例回归。安全和稳定性方面有 Safety Guard、Admin Token、traceId、超时和有界并发。项目已完成公网隔离部署和短时受控验证，但正式认证、高可用和完整可观测性仍是后续工作。
