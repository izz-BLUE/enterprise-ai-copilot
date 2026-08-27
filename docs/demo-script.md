# Demo Script

这是面向快速展示的短脚本；完整的本地启动、外部审批和故障排查见 [demo-guide.md](demo-guide.md)，面试口径见 [interview/demo-script.md](interview/demo-script.md)。

## 1. 先讲一句

> 这是一个 Java authority + Python Agent 的企业 RAG 平台。AI 可以读取差旅事实并生成报销 Proposal，但最终确认、业务写入、外部审批状态和恢复都由 Java 与持久化状态控制。

## 2. 展示顺序

1. 打开 `http://localhost:5173`，展示登录和 Agent 模式。
2. 输入企业制度问题，说明 FAISS + BM25 + RRF 返回答案与 sources。
3. 输入高风险问题，展示 Safety Guard 在 Planner 前拒答。
4. 输入差旅报销请求，展示 MCP facts → deterministic Proposal → `WAITING_USER`。
5. 点击 Confirm，说明 Java 在 confirm-time 重新验证并写 ExpenseClaim；此时状态进入 `WAITING_EXTERNAL`。
6. 在 Mock OA approve/reject，说明通知不带 status，Java 仍通过 authoritative GET 更新本地状态。
7. 展示 external resume 收口 Graph END，并强调 Java 终态先提交，resume 失败可重试但不回滚。
8. 以年假 Proposal/Confirm/Cancel 作为较短的第二个动作示例。

## 3. 必须讲清的边界

- Planner 只有规划权；`leave_proposal_tool`/`expense_proposal_tool` 不写业务数据。
- `WAITING_USER` 与 `WAITING_EXTERNAL` 是两个不同的 durable interrupt。
- Memory 是会话任务连续性，不是权限或业务真相；ACTIVE Memory 本身不触发 Extractor。
- `tool_history` 是本轮执行；`execution_history` 是有界 `CONTEXT_ONLY`；Checkpoint 是执行现场；Java PostgreSQL 才是业务事实。
- Mock OA 是独立 SQLite 模拟服务；当前实现是小规格单机验证，不承诺生产 SLA。
