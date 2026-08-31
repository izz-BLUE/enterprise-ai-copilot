# Quality Assurance and Verification Baseline

本文记录项目最终文档收口采用的验证口径。测试结果是已接受的工程基线，不等价于生产 SLA、长期容量或真实 OA 集成承诺。

## 1. Accepted baseline

| 范围 | 结果 |
|---|---:|
| Java backend | 334 passed |
| Python full suite | 1402 passed + 34 expected skips |
| PostgreSQL checkpoint integration | 17 passed |
| PostgreSQL crash recovery | 7 passed |
| PostgreSQL HITL | 5 passed |
| PostgreSQL external resume | 5 passed |
| PostgreSQL persistent runtime total | 34 passed, 0 skipped |
| Enterprise OA MCP | 24 passed |
| Mock OA | 17 passed |
| Frontend | 44 passed |
| Lint/build | pass |

## 2. Repository automation

### CI (`.github/workflows/ci.yml`)

- **Java Backend**：JDK 17，Maven compile 和 `./mvnw test`；
- **Mock OA Webhook**：Mock OA pytest、Ruff、local Compose config validation；
- **Python RAG Evaluation**：Python full suite、PostgreSQL Checkpoint/Crash/HITL/External Resume 集成、baseline retrieval gate、rule rewrite retrieval evaluation；
- **Frontend Build**：`npm ci`、production build、lint；
- **Frontend Browser Tests**：Chromium 安装和 Playwright E2E；

### Separate security workflows

- `.github/workflows/secret-scan.yml`：Gitleaks；
- `.github/workflows/codeql.yml`：Analyze `java-kotlin`、`python`、`javascript-typescript`。

### Dependency automation

- `.github/dependabot.yml`：GitHub Actions、Maven、uv 和 npm 的月度依赖检查；这是依赖自动化，不是 CI job。

## 3. RAG evaluation

固定评估集包含 38 个 case，区分：

- Retrieval：source hit、keyword hit、final case outcome，不调用 LLM；
- Generation：expected answer keywords、no-answer refusal、flaky retry；
- Regression：baseline/current report 对比，检测退化。

命令：

```bash
cd agent-python
uv run python scripts/eval/run_rag_eval.py
uv run python scripts/eval/run_rag_eval.py --with-baseline
```

评估集规模有限；通过率不能外推到所有企业文档、所有模型版本或生产 QPS。

## 4. Runtime and workflow verification

重点验证范围：

- Java authority：PendingAction nonce、TTL、owner、幂等、锁、业务事务和 stale confirmation；
- Python Agent：Planner schema、Tool visibility、Tool budget、success-signature dedupe、Safety Guard；
- Checkpoint：PostgresSaver setup、同步 durability、latest snapshot recovery、`graph.invoke(None)`；
- HITL：`WAITING_USER` marker/correlation、Java commit 后 `Command(resume)`；
- External approval：Mock OA PENDING→terminal、HMAC webhook、authoritative GET、reconciliation 和 external resume；
- Memory：ACTIVE read、trigger policy、`UPSERT + ACTIVE` proposal、Java terminal lifecycle；
- Frontend：聊天、Markdown、Safety、错误、确认卡和滚动回归。

## 5. Operational safety

- Java/Python 都有有界并发和超时；busy/overload 以稳定 429 和 `Retry-After` 反馈；
- Java 生成服务端 traceId，错误响应不暴露 exception message、secret、nonce digest 或 webhook raw body；
- Python、内部 Java API、Mock OA admin API 不作为公网业务入口；
- `PHOENIX_TRACING` 默认关闭，启用时旁路导出失败不阻断业务；
- `BUSINESS_ACTIONS_ENABLED`、Memory 写入和 Mock OA provider 默认关闭；reconciliation 与 external resume retry worker 始终低频调度并由 provider gateway fail-closed。

## 6. Accepted limitations

- 当前验证是小规格、单机、短时受控验证，没有生产 SLA；
- Rule-based Safety Guard 不是完整的 prompt-injection/content-safety 方案；
- Java/Python thread guard 是 process-local，不覆盖多实例；
- Java 本地事务与真实 OA 没有分布式事务，Mock OA 只是模拟；
- Enterprise OA MCP 是 fixture-backed read-only 集成，生产凭据和正式 OA 集成未验收；
- confirm-time revalidation 与本地 commit 之间存在小型 TOCTOU 窗口；
- 浏览器、评估集、容量和集中式 metrics/alerting 覆盖有限。

## 7. Reproducible commands

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
