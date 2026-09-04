# Phase D1 Semantic Routing Eval

本目录评估 Phase C Planner-first 生产路由的首个语义决策。评估器每条 case 只执行以下步骤：

1. 根据运行画像调用正式 `authorized_tools()` Capability Gate。
2. 使用当前 Tool Catalog 和 `build_planner_system_prompt()` / `build_planner_prompt()` 生成 Planner 输入。
3. 解析并调用 `PlannerDecision` 的严格 Schema 校验。
4. 停止；不构造 Graph、不调用 Workflow Guard、不执行 Tool、不访问 Java/MCP/RAG/Memory/Checkpoint。

这意味着报告衡量的是 Planner 的首步选择，而不是完整业务流程的执行结果。legacy Router-first 图仅作为直接测试/离线兼容实现保留，不由本评估器替代。

## 语料

`routing_cases.jsonl` 包含 130 条人工编写的中文自然语言 case，覆盖：

- `knowledge_rag`：15
- `leave_live_read`：18
- `leave_proposal`：15
- `expense_knowledge`：12
- `expense_live_read`：18
- `expense_proposal`：15
- `cross_domain`：10
- `negative_unsupported`：12
- `permission_boundary`：15

每条 case 的 `expected.tool_names` 表示允许的首步集合；多 Tool 场景允许多个合法首步。语料不提供 Tool 清单，候选集始终由运行画像和正式 Capability Gate 计算。

## 运行

默认模式是 deterministic stub，只验证语料、Prompt、Schema、评分和报告链路，不产生模型费用：

```powershell
cd agent-python
uv run python evals/routing/run_routing_eval.py --runs 3 --output routing_eval_report.json
```

真实模型评估必须显式开启，并建议每条 case 重复三次：

```powershell
cd agent-python
uv run python evals/routing/run_routing_eval.py --live --runs 3 --output routing_eval_report.json
```

`--live` 需要已有 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`；评估器不会创建或打印凭据。没有配置时会输出 `LIVE EVAL NOT RUN`，并将推荐标为 `EVAL_NOT_RUN`。不要在 CI 中启用 `--live`。

报告包含终端摘要以及机器可读 JSON：总体/分类准确率、Read-vs-Proposal、Schema Valid、稳定性 `3/3、2/3、1/3、0/3` 分桶、越权选择和失败分类。报告只保存 case 文本、脱敏后的决策摘要和安全断言，不保存 Prompt、模型原始响应、身份、Token 或业务数据。

## 门槛

真实 Live Eval 的建议门槛为：总体及 Knowledge/Live Read 至少 95%，Read-vs-Proposal 至少 98%，Schema Valid 至少 99%，Unauthorized Selection 为 0。达到门槛时 Recommendation 为 `PASS`，否则为 `NEEDS_ROUTING_TUNING`；本目录只报告问题，不修改 Planner 或生产路由。
