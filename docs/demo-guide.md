# 多用户请假 Demo Guide

本手册用于隔离的本地或受控演示环境。身份选择器和共享 Admin Token 都不是生产认证机制，不得直接开放到生产环境。

## 1. 前置要求

- Git；
- Java 17，Java 依赖由 Maven Wrapper 管理；
- Python 3.11 和 `uv`；
- Node.js 20 或更高版本、npm；
- Docker Desktop 或兼容 Docker Engine，Docker Compose v2；
- PowerShell 7。

生产 Compose 还需要仓库外的 ONNX 模型和 RAG 数据，目录结构见 [Deployment](deployment.md)。模型、索引和 Secret 均不进入 Git。

## 2. 获取代码与安装依赖

```powershell
git clone https://github.com/izz-BLUE/enterprise-ai-copilot.git
Set-Location enterprise-ai-copilot

Set-Location agent-python
uv sync
Set-Location ..\frontend
npm ci
Set-Location ..\backend-java
.\mvnw.cmd -v
Set-Location ..
```

构建本次演示使用的本地镜像：

```powershell
docker build -t enterprise-ai-copilot-python:demo -f agent-python/Dockerfile agent-python
docker build -t enterprise-ai-copilot-java:demo -f backend-java/Dockerfile backend-java
$env:PYTHON_IMAGE = "enterprise-ai-copilot-python:demo"
$env:JAVA_IMAGE = "enterprise-ai-copilot-java:demo"
```

Compose 使用外部网络 `deploy_eat-what-net`。仅在网络不存在时创建：

```powershell
docker network inspect deploy_eat-what-net *> $null
if ($LASTEXITCODE -ne 0) { docker network create deploy_eat-what-net | Out-Null }
```

## 3. 当前进程环境变量

以下都是占位值。替换后只保存在当前 PowerShell 进程，不要提交 `.env`，不要在录屏、截图或日志中展示 Secret。

```powershell
$env:POSTGRES_PASSWORD = "<generate-a-strong-password>"
$env:ADMIN_TOKEN = "<your-admin-token>"
$env:DEEPSEEK_API_KEY = "<your-provider-key>"
$env:DEEPSEEK_BASE_URL = "<your-provider-base-url>"
$env:DEEPSEEK_MODEL = "<your-provider-model>"
$env:BUSINESS_ACTIONS_ENABLED = "false"
$env:BUSINESS_ACTIONS_REQUIRE_ADMIN = "true"
$env:DEMO_IDENTITY_ENABLED = "false"
```

模型和数据必须位于 Compose 声明的仓库外只读挂载路径。修改路径时使用仓库外临时 Compose override，不要提交本机绝对路径。

## 4. 默认安全启动

```powershell
$project = "eac-p0-final-remediation"
$compose = "deploy/docker-compose.prod.yml"
$javaBase = "http://127.0.0.1:18088"
$portOverride = Join-Path $env:TEMP "eac-p0-final-remediation-port.override.yml"
@'
services:
  java-backend:
    ports: !override
      - "127.0.0.1:18088:8080"
'@ | Set-Content -Encoding utf8 $portOverride
$overrideArgs = @('-f', $portOverride)

docker compose -p $project -f $compose @overrideArgs config --quiet
docker compose -p $project -f $compose @overrideArgs up -d --build
docker compose -p $project -f $compose @overrideArgs ps
```

验证 Java 健康和默认关闭状态：

```powershell
(Invoke-WebRequest "$javaBase/api/health" -SkipHttpErrorCheck).StatusCode
(Invoke-WebRequest "$javaBase/api/demo/identities" -SkipHttpErrorCheck).StatusCode
```

预期健康接口为 `200`，身份目录为 `503 DEMO_IDENTITY_DISABLED`；Business Actions 和 Demo Identity 默认均关闭。标准 RAG 不要求 Demo 身份，可以正常使用；Provider 或模型资产不可用时必须返回安全降级响应，不得伪造成功。

## 5. 开启受控演示模式

```powershell
docker compose -p $project -f $compose @overrideArgs down
$env:BUSINESS_ACTIONS_ENABLED = "true"
$env:BUSINESS_ACTIONS_REQUIRE_ADMIN = "true"
$env:DEMO_IDENTITY_ENABLED = "true"
docker compose -p $project -f $compose @overrideArgs config --quiet
docker compose -p $project -f $compose @overrideArgs up -d --build
(Invoke-WebRequest "$javaBase/api/health" -SkipHttpErrorCheck).StatusCode
```

前端在另一个 PowerShell 窗口启动：

```powershell
Set-Location frontend
npm run dev
```

浏览器访问 `http://localhost:5173`。Admin Token 只输入演示页面内存，不写入 URL、Cookie、localStorage 或 sessionStorage。

## 6. 动态计算未来工作日

```powershell
function Get-NextWorkday([datetime]$From) {
    $day = $From.Date.AddDays(1)
    while ($day.DayOfWeek -in @('Saturday', 'Sunday')) { $day = $day.AddDays(1) }
    $day
}
$nextWorkday = Get-NextWorkday (Get-Date)
$followingWorkday = Get-NextWorkday $nextWorkday
$nextDate = $nextWorkday.ToString('yyyy-MM-dd')
$followingDate = $followingWorkday.ToString('yyyy-MM-dd')
```

当前 Demo 只排除周六、周日，不处理中国法定节假日和调休。

## 7. 核心演示

### User A Confirm

1. 选择 **Demo User A**；
2. 输入：`申请 $nextDate 一天年假，原因为个人事务`；
3. 检查确认卡姓名、日期、天数、余额前后值；
4. 点击 Confirm；
5. 确认 `SUCCEEDED` 且返回 requestId，User A 余额只扣减一次。

### Cancel

1. User A 使用 `$followingDate` 生成新 Pending；
2. 点击 Cancel；
3. 确认 `CANCELLED`，余额不变且没有新增 LeaveRequest。

### 多用户和 Manager 边界

User B 可以申请与 User A 相同日期；冲突按 employeeId 隔离。Manager 只能创建、确认或取消自己的草稿，没有审批、查看或操作 A/B 申请的权限。

跨用户验证不得在文档、命令历史或截图中复制 nonce。使用真实 PostgreSQL 集成测试：

```powershell
Set-Location backend-java
.\mvnw.cmd "-Dtest=DemoIdentityIsolationIntegrationTest" test
Set-Location ..
```

测试断言 B 和 Manager 对 A 的 Confirm/Cancel 均得到与不存在 Action 相同的 `404 ACTION_NOT_FOUND`，且 A 的草稿、余额和申请不变。

## 8. 幂等重放

首次 Confirm 成功后，Action 的持久化成功结果成为权威结果。后续使用相同或不同的格式合法 UUID `Idempotency-Key` 再次确认，均返回原 requestId 和 `replayed=true`，不会再次创建 LeaveRequest 或扣减余额。

为避免手工复制 nonce、Admin Token 和完整 ID，使用定向集成测试：

```powershell
Set-Location backend-java
.\mvnw.cmd "-Dtest=BusinessActionPersistenceIntegrationTest#confirmPersistsAndReplaysSameResultForAnyValidKey" test
Set-Location ..
```

该测试同时断言首次 `replayed=false`、同 Key/不同合法 Key 重放、requestId 相同、LeaveRequest 只有一条且余额只扣一次。

## 9. Java 重启恢复

1. User A 创建 Pending，保持浏览器页面不刷新；nonce 只存在当前页面内存；
2. 只重启 Java：

```powershell
docker compose -p $project -f $compose @overrideArgs restart java-backend
docker compose -p $project -f $compose @overrideArgs ps java-backend
```

3. Java 恢复健康后，由原 User A 在原页面点击 Confirm；
4. 刷新浏览器会丢失 nonce，刷新后必须重新生成草稿，这是预期安全行为。

## 10. PostgreSQL Named Volume 恢复

先记录页面显示的余额与状态，不输出完整业务 ID。停止并重建 PostgreSQL 容器，但保留 Named Volume：

```powershell
docker compose -p $project -f $compose @overrideArgs stop java-backend postgres
docker compose -p $project -f $compose @overrideArgs rm -f postgres
docker compose -p $project -f $compose @overrideArgs up -d postgres
docker compose -p $project -f $compose @overrideArgs up -d java-backend
```

重新检查余额、Action 状态和 LeaveRequest。恢复验证中严禁执行 `docker compose down -v`，因为 `-v` 会删除持久化数据。

## 11. 数据库故障与恢复

```powershell
docker compose -p $project -f $compose @overrideArgs stop postgres
(Invoke-WebRequest "$javaBase/api/health" -SkipHttpErrorCheck).StatusCode
docker compose -p $project -f $compose @overrideArgs start postgres
docker compose -p $project -f $compose @overrideArgs ps postgres
(Invoke-WebRequest "$javaBase/api/health" -SkipHttpErrorCheck).StatusCode
```

预期停库后为 `503`，恢复后为 `200`，响应不得包含 JDBC URL、数据库用户名、密码、SQL 或堆栈。

## 12. 无 Provider Secret 时

没有有效 Provider Key 时，不得宣称真实 Provider Smoke 通过。仍可运行 Java、Python、前端、PostgreSQL 和 Mock Provider 测试：

```powershell
Set-Location agent-python
cmd /d /c "set DEEPSEEK_API_KEY=& set DEEPSEEK_BASE_URL=& set DEEPSEEK_MODEL=& uv run python -m pytest -q"
Set-Location ..\backend-java
.\mvnw.cmd clean test
Set-Location ..\frontend
npm run lint
npm run build
npm run test:e2e
```

## 13. 停止与清理

普通停止会保留 Named Volume：

```powershell
docker compose -p $project -f $compose @overrideArgs down
Remove-Item -LiteralPath $portOverride
```

仅在明确不再需要演示数据时执行完全清理。以下命令会永久删除本项目 Volume：

```powershell
docker compose -p $project -f $compose @overrideArgs down -v
Remove-Item -LiteralPath $portOverride
```

清理只使用本手册的独立项目名，不执行全局容器、网络、Volume 或镜像清理命令。

## 14. 真实 OA 边界

当前 `PostgresLeaveSandboxGateway` 与 Action、账户参加同一个本地 PostgreSQL 事务，本项目没有发送任何真实 OA 请求。真实 OA 网络调用不能加入本地数据库事务，不能只替换 Gateway 就宣称安全上线；后续至少需要 Transactional Outbox、异步投递、外部幂等、重试、回调或轮询、对账、补偿和状态映射。
