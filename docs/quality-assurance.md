# 质量保证与验证基线

本文记录项目当前的验证范围与可复现门禁。通过数量会随测试集演进；未绑定具体 SHA 或 release 的历史计数不作为 current main 的固定基线。所有结果都不等价于生产 SLA、长期容量或真实 OA 集成承诺。

## 1. 已接受验证范围

| 范围 | 当前验证口径 |
|---|---:|
| Java 后端 | CI 完整测试 |
| Python 完整套件 | CI 完整测试（含集成与预期跳过） |
| PostgreSQL checkpoint 集成 | CI 集成验证 |
| PostgreSQL crash recovery | CI 集成验证 |
| PostgreSQL HITL | CI 集成验证 |
| PostgreSQL external resume | CI 集成验证 |
| PostgreSQL 持久化 runtime 合计 | CI 持久化 runtime 集成验证 |
| Enterprise OA MCP | fixture-backed read-only 集成验证 |
| Mock OA | pytest、webhook 与配置校验 |
| 前端 | CI build 与 browser tests |
| Lint/build | CI 检查 |

## 2. 仓库自动化

### CI (`.github/workflows/ci.yml`)

- **Java Backend**：JDK 17，Maven compile 和 `./mvnw test`；
- **Mock OA Webhook**：Mock OA pytest、Ruff、本地 Compose 配置校验；
- **Python RAG Evaluation**：Python 完整套件、PostgreSQL Checkpoint/Crash/HITL/External Resume 集成、基线检索门禁、规则重写检索评估；
- **Frontend Build**：`npm ci`、生产构建、lint；
- **Frontend Browser Tests**：Chromium 安装和 Playwright E2E；

### 独立安全工作流

- `.github/workflows/secret-scan.yml`：Gitleaks；
- `.github/workflows/codeql.yml`：Analyze `java-kotlin`、`python`、`javascript-typescript`。

### 依赖自动化

- `.github/dependabot.yml`：GitHub Actions、Maven、uv 和 npm 的月度依赖检查；这是依赖自动化，不是 CI job。

## 3. RAG 评估

固定评估集包含 38 个 case（28 个 answerable、10 个 no-answer），区分：

- Retrieval（检索）：source hit、keyword hit、final case outcome，不调用 LLM；
- Generation（生成）：expected answer keywords、no-answer refusal、flaky retry；
- Regression（回归）：对比 baseline/current report，检测退化。

命令：

```bash
cd agent-python
uv run python scripts/eval/run_rag_eval.py
uv run python scripts/eval/run_rag_eval.py --with-baseline
```

评估集规模有限；通过率不能外推到所有企业文档、所有模型版本或生产 QPS。

## 4. Eval 分层与 CI 关系

当前评估素材按职责分为四层：

| Eval | 当前规模 | 运行形态 | 是否为专门的 CI 门禁 |
| --- | ---: | --- | --- |
| 离线 Agent Eval | 18 case | mock Planner/Tool，验证 loop、预算、去重和权限反应 | 作为 Python full pytest 的测试覆盖；没有单独 CLI job |
| 路由 Eval | 130 case、9 类别、5 runtime profile | 只评 Planner 首次决策；完整语料运行需手工执行 `evals/routing/run_routing_eval.py` | 否；CI 覆盖其契约/评估器测试，不运行完整路由报告 |
| 真 LLM Agent Eval | 24 case、8 类别 | 真模型 + 确定性 Tool stub；执行 `scripts/eval/run_agent_real_eval.py` | 否，属于手工或发布前验证 |
| RAG Eval | 38 case | Retrieval/Generation；CI 执行生产窄规范化 Retrieval gate | 是：`python-eval` job 仅对生产 Retrieval gate 以退出码门禁 |

CI 主工作流当前有 5 个 job：Java Backend、Mock OA Webhook、Python RAG Evaluation、Frontend Build、Frontend Browser Tests。Gitleaks 和 CodeQL 在独立 workflow 中运行，Dependabot 是依赖自动化，不计入 CI job。评估 case 数、测试通过数和压测结果会随代码与语料演进；需要新鲜结果时运行对应命令或查看具体 CI/release 记录。

## 5. Runtime 与工作流验证

重点验证范围：

- Java authority（Java 权威）：PendingAction nonce、TTL、owner、幂等、锁、业务事务和 stale confirmation；
- Python Agent：Planner schema、Tool visibility（Tool 可见性）、Tool budget（Tool 预算）、success-signature dedupe、Safety Guard；
- Checkpoint：PostgresSaver setup、同步 durability、latest snapshot recovery、`graph.invoke(None)`；
- HITL：`WAITING_USER` marker/correlation、Java commit 后 `Command(resume)`；
- External approval：Mock OA PENDING→terminal、HMAC webhook、authoritative GET、reconciliation 和 external resume；
- Memory：ACTIVE 读取、trigger policy（触发策略）、`UPSERT + ACTIVE` proposal、Java 终态生命周期；
- Frontend：聊天、Markdown、Safety、错误、确认卡和滚动回归。

## 6. 运维安全

- Java/Python 都有有界并发和超时；busy/overload 以稳定 429 和 `Retry-After` 反馈；
- Java 生成服务端 traceId，错误响应不暴露 exception message、secret、nonce digest 或 webhook raw body；
- Python、内部 Java API、Mock OA admin API 不作为公网业务入口；
- `PHOENIX_TRACING` 默认关闭，启用时旁路导出失败不阻断业务；
- `BUSINESS_ACTIONS_ENABLED`、Memory 写入和 Mock OA provider 默认关闭；reconciliation 与 external resume retry worker 始终低频调度并由 provider gateway fail-closed。

## 7. 已接受限制

- 当前验证是小规格、单机、短时受控验证，没有生产 SLA；
- Rule-based Safety Guard 不是完整的 prompt-injection/content-safety 方案；
- Java/Python thread guard 是 process-local，不覆盖多实例；
- Java 本地事务与真实 OA 没有分布式事务，Mock OA 只是模拟；
- Enterprise OA MCP 是 fixture-backed read-only 集成，生产凭据和正式 OA 集成未验收；
- confirm-time revalidation 与本地 commit 之间存在小型 TOCTOU 窗口；
- 浏览器、评估集、容量和集中式 metrics/alerting 覆盖有限。

## 8. 可复现命令

```bash
cd backend-java
./mvnw test

cd agent-python
uv run pytest

cd frontend
npm ci
npm run lint
npm run build
npm run test:e2e

cd mock-oa
python -m pytest -q
```

POSTGRES 集成测试需要可用 PostgreSQL 和 `LANGGRAPH_CHECKPOINT_DSN`；真实 Enterprise OA 依赖外部 MCP fixture/service，不能用本地单元测试替代正式集成验收。
