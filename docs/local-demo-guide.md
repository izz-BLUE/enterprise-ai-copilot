# 本地 Demo 指南（快速开始）

这里保留最短的本地启动路径；完整的差旅报销、Mock OA、HITL/external resume 和故障排查见 [demo-guide.md](demo-guide.md)。

## 前置条件

Java 17、Python 3.11、uv、Node 20、npm、Docker Compose、PostgreSQL 和可用的 DeepSeek API key。需要外部报销演示时，还需要 Enterprise OA MCP fixture/service 和 LangGraph checkpoint PostgreSQL。

## 启动基础设施

```bash
docker compose -f deploy/docker-compose.local.yml up -d postgres mock-oa
```

## 启动服务

```bash
# Terminal 1
cd agent-python
uv sync --group dev
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2
cd backend-java
./mvnw spring-boot:run

# Terminal 3
cd frontend
npm ci
npm run dev
```

Windows PowerShell 的 Java wrapper 可使用 `.\mvnw.cmd spring-boot:run`。

地址：React `http://localhost:5173`，Java `http://localhost:8080`，Python `http://localhost:8000`，Mock OA `http://localhost:8010`。

## 健康检查与 RAG smoke

```bash
curl http://localhost:8080/api/ready
curl http://localhost:8080/api/agent/ready
curl http://localhost:8000/agent/ready

curl -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"病假需要提供哪些材料？"}'
```

普通用户和浏览器只访问 Java；生产 Compose 不把 Python 8000 映射到宿主机公网。

## 持久化工作流前置条件

Python 本地启动即要求 PostgreSQL 执行快照 DSN；演示可恢复用户确认和外部审批时设置：

```text
LANGGRAPH_CHECKPOINT_DSN=postgresql://<user>:<password>@localhost:5432/<db>
ENTERPRISE_OA_MCP_URL=http://127.0.0.1:8100/mcp
```

Java 侧按 [demo-guide.md](demo-guide.md) 打开 `DEMO_AUTH_ENABLED`、`BUSINESS_ACTIONS_ENABLED` 和 `MOCK_OA_ENABLED`，并为 `demo`、`zhangsan`、`admin` 分别配置 `DEMO_PUBLIC_PASSWORD`、`DEMO_INTERVIEW_PASSWORD`、`DEMO_ADMIN_PASSWORD`；`DEMO_AUTH_DEFAULT_PASSWORD` 仅用于 lisi/wangwu legacy seed。前端公开 demo 的 `VITE_PUBLIC_DEMO_USERNAME` / `VITE_PUBLIC_DEMO_PASSWORD` 会进入浏览器构建产物，不能填入 server-side password。外部 retry/reconciliation worker 始终低频调度，provider 关闭时 gateway fail-closed；所有功能默认关闭是安全基线。

## 恢复 zhangsan 演示状态

脚本固定只针对 `U10001/zhangsan/E10001` 清理 Task Runtime、PendingAction、LeaveRequest、ExpenseClaim、PurchaseRequest、Conversation Memory、LangGraph checkpoint 和 Mock OA approval，并把年假余额恢复为固定基线 `10.0`。执行前会先采集目标 ID，检查身份与关联一致性；任一异常都会 fail-closed，不会部分清理。执行前建议先停止 Java/Python 写入流量。

```bash
bash deploy/reset-demo-state.sh --dry-run
bash deploy/reset-demo-state.sh --yes
```

不带 `--yes` 时，脚本会要求交互输入 `RESET`；`--dry-run` 只执行 PostgreSQL `SELECT` 和严格只读 SQLite 查询。Mock OA 通过 `ExpenseClaim.external_request_id -> expense_approval.request_id` 精确关联；Enterprise OA 的 `TRIP-20260818-001`、`INV-001`、`INV-002` 属于外部 fixture，脚本会输出 `EXTERNAL_FIXTURE_VERIFICATION = NOT_APPLICABLE`，由后续 Smoke 验证可用性。脚本不访问或修改 Flyway migration 和 sequence。

## 故障排查

| 现象 | 优先检查 |
|---|---|
| Python ready 失败 | API key、索引、checkpoint DSN 和 PostgreSQL health |
| Proposal 缺少事实 | `ENTERPRISE_OA_MCP_URL`、fixture ownership、trip/invoice 状态 |
| Confirm 503 | Python revalidation adapter 或 OA MCP 是否可用；不要把 PendingAction 改成失败 |
| OA webhook 失败 | 精确 path、raw-body HMAC、共享 secret、timestamp window |
| 同一请求 busy | Java/Python process-local runtime guard 正在保护完整 lifecycle |

## 相关文档

- [完整 Demo 指南](demo-guide.md)
- [API](api.md)
- [部署](deployment.md)
- [质量保证](quality-assurance.md)
- [README](../README.md)
