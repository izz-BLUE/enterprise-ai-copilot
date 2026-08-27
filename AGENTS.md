# AGENTS.md

企业级 RAG + Agent 业务流程辅助平台（Java + Python 双服务 + React 前端）。
本文件只保留长期稳定的事实与工作纪律；架构 / API / 配置 / 评估参数等细节
按路径导航到 canonical source，不在此复制。动态状态（branch / commit / 临时配置）一律不写入。

## 三端职责

- Java Spring Boot（backend-java，:8080）：企业业务主系统 —— 用户权限、知识库管理、审计日志、业务流程、业务动作确认
- Python FastAPI（agent-python，:8000）：AI Agent 服务 —— RAG、LangGraph Agent、Tool Calling、Prompt 编排
- React + Vite（frontend，:5173）：前端界面

## 关键 invariant

- **Java authority boundary**：受控业务动作必须经 Java 侧 PendingAction 持久化 + nonce 校验 + 幂等确认才执行，默认关闭；ADMIN_TOKEN 为空 = Demo 模式（eval 路由全开放），生产必须设置。
- **Python Agent Graph**：main 同时保留两套互斥状态图。`AGENT_LOOP_ENABLED=true`（仓库部署默认）走 Planner-first：`safety → planner ⇄ tool_executor → finalize`，Planner 拥有规划权、无最终业务执行授权，预算受 `MAX_PLANNER_STEPS=5` / `MAX_TOOL_CALLS=3` 收敛；`AGENT_LOOP_ENABLED=false`（显式回退）走 legacy Router-first：`safety → router → rag|eval|action|refuse`，意图路由 + 规则工具。Planner-first **最多支持 5 个 Tool**，实际可见集合由程序层按权限动态收缩，**模型不能自行扩大 Tool 权限**：
  - 始终可见：`rag_answer_tool`；`employee_id`、`JAVA_BASE_URL`、`JAVA_INTERNAL_TOKEN` 均非空时追加 `leave_balance_tool` / `leave_request_tool`
  - `allow_eval=true` 时追加：`eval_report_tool`
  - `allow_business_actions=true` 且 `employee_id` 非空时追加：`leave_proposal_tool`
  legacy Router-first 仅暴露部分 Tool，不走 `leave_proposal_tool`。
- **可信系统字段边界**：`employee_id` / `business_date` / `trace_id` / 请求 deadline 由每次请求的 Runtime Context 注入，不属于可保存的 `AgentState`，也不进入 LLM `arguments`；Planner 决策结构由 Pydantic 严格白名单校验；Tool Executor 独立做权限 / Tool 预算 / 成功签名去重校验。
- **P3-1 / P3-2 / P3-3 执行快照边界**：Java 基于可信 `VerifiedIdentity.userId()` 与已解析 `conversationId` 生成 `X-Agent-Thread-Id`；Python POSTGRES 模式在启动时复用 `ConnectionPool` / `PostgresSaver` / 两套持久化图，节点 `durability="sync"` 落盘。Checkpoint 只记录执行现场，不是业务事实或权限来源；Planner-first 新执行保存 strict `execution_recovery` marker，同一 thread 的 exact same unfinished request 通过 latest `snapshot.next`、marker/date/pending-node/replay-safe 校验后以 `graph.invoke(None)` 恢复，重新注入当前 Runtime Context；completed execution、legacy deterministic graph、interrupt 与不安全状态不自动恢复。Resume 保留当前 execution 的 `tool_history`、计数和 execution_id；P3-2 `execution_history` 只保存 travel/invoice 成功步骤的有界 `CONTEXT_ONLY` 摘要，Fresh 运行仅在 ACTIVE Memory + task type 匹配时 hydrate，不进入当前 Tool 去重、ExpenseProposalContext 或 Memory Trigger。Java `AgentRuntimeThreadExecutionGuard` 在 Memory Read 前按最终 runtime thread 串行化完整 Java Agent 生命周期；Python guard 继续保护 recovery inspection 到最终 Checkpoint。
- **`leave_proposal_tool` 定位**：Planner-first 下生成 `action_proposal` 或 `missing_fields`（Clarification），**不执行写操作**；`confirmationNonce` 与 `PendingAction` 持久化、状态机、TTL、幂等、权限和最终数据库写入全部在 Java 侧完成。`leave_proposal_tool` **不依赖** `JAVA_BASE_URL` / `JAVA_INTERNAL_TOKEN`；这两个变量只属于 `leave_balance_tool` / `leave_request_tool` 的 Python → Java 内部只读链路。
- **Safety Guard Lite 定位**（Python safety_node）：启发式纵深防御过滤器，不是 authorization / trust / tool permission / business validation 边界；原始用户输入始终原样传给下游。
- **RAG / 数据权限边界**：数据分目录隔离（data/hr|bank|it 原始知识库 / data/processed 构建产物 / data/eval 评估用例）；Python 只做检索与生成，业务数据写操作只能走受控业务动作链路。
- **请求链路**：前端 → Java (8080) → Python (8000)；普通 RAG 走 `/agent/chat`，Agent 走 `/agent/langgraph/chat`，业务动作确认走 `/api/agent/actions/{id}/confirm`。
- **Phoenix 可观测性边界**：`PHOENIX_TRACING` 默认关闭；启用时以 OpenTelemetry/OpenInference + BatchSpanProcessor 旁路追踪 Python AI 请求，初始化/导出失败不得阻断业务。默认不采集 Prompt、用户输入、检索正文或模型输出；`business_trace_id` 只用于关联定位，不是身份、权限或业务事实来源。现有离线评估仍是回归门禁。
- **公网状态约束**：仓库对公网实际运行版本无证据，文档统一表述"仓库部署默认 Planner-first（false 回退 legacy）"；公网是否启用 Planner-first / 受控业务动作以运维 `.env` 为准，本文件不据此做能力宣称。

## 高频命令

- 启动：agent-python `uv run uvicorn app.main:app --reload --port 8000`；backend-java `./mvnw spring-boot:run`；frontend `npm run dev`
- 测试：agent-python `uv run pytest`；backend-java `./mvnw test`（含 Testcontainers）；frontend `npm run test:e2e`
- 部署：deploy/docker-compose.prod.yml

## 文档导航（canonical source）

- 架构细节 → docs/architecture.md；业务动作 → docs/controlled-business-actions.md
- API 契约 → docs/api.md
- 完整配置 → agent-python/.env.example 与 backend-java/src/main/resources/application.properties
- 评估参数与指标 → docs/quality-assurance.md 与 scripts/eval/

## 项目 Skill 触发导航（SOP 见各 SKILL.md，不复制）

- spring-boot-review：改动/审查 Java Controller、Service、异常处理、API 契约
- fastapi-agent-review：改动/审查 Python Agent 模块、DeepSeek 调用、Java-Python 契约
- rag-llm-architecture：设计/审查 RAG 链路、检索质量、幻觉控制、评估
- project-delivery-check：提交 / 推送 / 发布 / 展示前执行交付门禁
- ai-project-interviewer：面试视角评估本项目

## 工作纪律

- 最小必要修改：不顺手重构无关代码，不引入无关依赖。
- 不通过跳过测试 / 删除测试 / 降低断言掩盖失败；测试失败先查根因。
- 未经用户授权不 commit / push / reset / clean。
- 可廉价验证的假设（命令、配置、行为）优先执行获取环境反馈，不空想。
- 同一检索目标不要跨工具重复执行（FastCtx 与原生搜索选一）。
- 跨服务契约变更（路由 / 请求响应 / 错误码）必须同步两端与 docs/api.md。
- 安全 / 架构变更后同步更新本文件与 docs/architecture.md；本文件与 CLAUDE.md 并行生效，改动同步维护。
