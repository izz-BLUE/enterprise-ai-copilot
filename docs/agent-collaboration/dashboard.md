# 协作仪表盘（Collaboration Dashboard）

> **项目总控视图，每日更新。**

## 项目状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 项目定位 | 🟡 Demo 阶段 | 本地可复现，未部署公网 |
| 协作框架 | 🟢 建立中 | 架构 Owner 正在创建协作文档 |
| 代码质量 | 🟡 待加固 | 缺少单元测试 |
| Evaluation | 🟢 闭环 | 38 cases, 100% 通过率（基于当前 case 集） |
| 文档 | 🟢 较完整 | 需修正少量过时描述 |

## 当前活跃会话

| 角色 | 状态 | 当前任务 | 分支 |
|---|---|---|---|
| 架构 Owner | 🟢 活跃 | TASK-001（协作文档框架） | main |
| 全栈开发 | ⬜ 未启动 | — | — |
| AI/RAG 工程师 | ⬜ 未启动 | — | — |
| QA | ⬜ 未启动 | — | — |
| 安全 Review | ⬜ 未启动 | — | — |

## 任务进度

| Phase | 总数 | 完成 | 进行中 | 待领取 |
|---|---|---|---|---|
| Phase 1：协作框架 | 3 | 0 | 1 | 2 |
| Phase 2：模块盘点 | 4 | 0 | 0 | 4 |
| Phase 3：质量加固 | 5 | 0 | 0 | 5 |
| Phase 4：体验优化 | 5 | 0 | 0 | 5 |

## 风险项

| 风险 | 等级 | 说明 |
|---|---|---|
| DTO 契约无强类型 | 🟡 中 | Java ↔ Python 手动对齐 |
| 评估集规模小 | 🟡 中 | 38 个 case，覆盖有限 |
| 无单元测试 | 🟡 中 | Java 和 Python 都缺 |
| HuggingFace 离线 | 🟡 中 | 国内网络限制 |
| 多会话冲突 | 🟢 低 | 已建立模块所有权 |

## 快速链接

| 文档 | 路径 | 用途 |
|---|---|---|
| 项目上下文 | [00-project-context.md](00-project-context.md) | 启动前必读 |
| 架构边界 | [01-architecture-boundary.md](01-architecture-boundary.md) | 跨层修改前必读 |
| API 契约 | [02-api-contract.md](02-api-contract.md) | 改接口前必读 |
| Agent 注册表 | [03-agent-registry.md](03-agent-registry.md) | 注册会话 |
| 任务看板 | [04-task-board.md](04-task-board.md) | 领取任务 |
| 交接模板 | [05-session-handoff-template.md](05-session-handoff-template.md) | 会话结束时用 |
| 不可修改清单 | [06-do-not-touch.md](06-do-not-touch.md) | 开发前必读 |
| 发布检查 | [07-release-checklist.md](07-release-checklist.md) | 合并前必读 |
| 旧会话交接 | [handoff/legacy-dev-session-handoff.md](handoff/legacy-dev-session-handoff.md) | 了解历史 |

## 下一步

1. 架构 Owner 完成协作文档框架（TASK-001）
2. 启动全栈开发会话（TASK-010 + TASK-011）
3. 启动 AI/RAG 工程师会话（TASK-012 + TASK-013）
4. 盘点完成后启动 QA 和安全 Review
