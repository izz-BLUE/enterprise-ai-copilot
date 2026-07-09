# 03 - Agent 注册表（Agent Registry）

> **每个参与协作的 Claude Code 会话必须在此注册。**

## 注册格式

每个会话启动时，在下方添加一个条目：

```markdown
### [会话名称]

| 项目 | 值 |
|---|---|
| 角色 | （架构 Owner / 全栈开发 / AI/RAG 工程师 / QA / 安全 Review） |
| 启动时间 | YYYY-MM-DD HH:MM |
| 当前分支 | feat/xxx |
| 当前任务 | TASK-xxx |
| 负责模块 | （列出模块路径） |
| 状态 | 🟢 活跃 / 🟡 空闲 / 🔴 已退出 |
```

## 当前注册会话

### Architecture Owner

| 项目 | 值 |
|---|---|
| 角色 | 架构 Owner / Tech Lead |
| 启动时间 | 2026-07-10 |
| 当前分支 | main（仅协作文档，不改业务代码） |
| 当前任务 | TASK-001（建立协作文档框架） |
| 负责模块 | `docs/agent-collaboration/*` |
| 状态 | 🟢 活跃 |

---

## 角色职责矩阵

| 职责 | 架构 Owner | 全栈开发 | AI/RAG 工程师 | QA | 安全 Review |
|---|---|---|---|---|---|
| 协作文档维护 | ✅ Owner | 读 | 读 | 读 | 读 |
| 架构决策 | ✅ Owner | 建议 | 建议 | — | 建议 |
| Java 后端 | 审核 | ✅ Owner | ❌ | 读 | 读 |
| React 前端 | 审核 | ✅ Owner | ❌ | 读 | ❌ |
| Python AI 服务 | 审核 | ❌ | ✅ Owner | 读 | 读 |
| RAG Retrieval | 审核 | ❌ | ✅ Owner | 读 | ❌ |
| Evaluation | 审核 | ❌ | ✅ Owner | 读 | ❌ |
| Prompt 工程 | 审核 | ❌ | ✅ Owner | ❌ | ❌ |
| Smoke Test | 审核 | 配合 | 配合 | ✅ Owner | ❌ |
| 安全审查 | 审核 | 配合 | 配合 | ❌ | ✅ Owner |
| 知识库文档 | ❌ | ❌ | ❌ | ❌ | ❌ |
| Eval Baseline | 审核 | ❌ | ✅ Owner（需理由） | ❌ | ❌ |

## 模块所有权

| 模块路径 | Owner | 说明 |
|---|---|---|
| `docs/agent-collaboration/*` | 架构 Owner | 协作文档 |
| `docs/architecture.md` | 架构 Owner | 架构文档 |
| `docs/api.md` | 架构 Owner + 全栈 + AI | 接口契约 |
| `backend-java/src/**` | 全栈开发 | Java 后端 |
| `frontend/src/**` | 全栈开发 | React 前端 |
| `agent-python/app/retrieval/**` | AI/RAG 工程师 | 检索模块 |
| `agent-python/app/prompts/**` | AI/RAG 工程师 | Prompt 模块 |
| `agent-python/app/services/**` | AI/RAG 工程师 | 服务层 |
| `agent-python/app/agents/**` | AI/RAG 工程师 | Agent 模块 |
| `agent-python/app/guards/**` | AI/RAG 工程师 | Safety Guard |
| `agent-python/scripts/eval/**` | AI/RAG 工程师 | Evaluation |
| `agent-python/scripts/build/**` | AI/RAG 工程师 | 构建脚本 |
| `data/eval/rag_eval_cases.json` | AI/RAG 工程师 | 评估用例 |
| `data/eval/reports/*` | 自动产出 | 不应手动修改 |
| `data/hr/*`, `data/bank/*`, `data/it/*` | ❌ 无 Owner | 知识库文档，不修改 |

## 会话启动协议

新会话启动时：

1. 读 `00-project-context.md`
2. 读 `06-do-not-touch.md`
3. 读 `04-task-board.md` 领取任务
4. 在本文档注册会话信息
5. 创建 feature 分支
6. 开始工作

## 会话退出协议

会话结束时：

1. 更新本文档状态为 🔴 已退出
2. 更新 `04-task-board.md` 任务状态
3. 写 handoff 文档（使用 `05-session-handoff-template.md`）
4. 推送所有变更
