# 协作仪表盘（Collaboration Dashboard）

> **项目总控视图，每日更新。**

## 项目状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 项目定位 | 🟡 Demo 阶段 | 本地可复现，未部署公网 |
| 协作框架 | 🟢 已建立 | Phase 1~3 审查完成 |
| 代码质量 | 🟡 待加固 | 5 个 P0 + 9 个 P1 待修复 |
| 安全状态 | 🔴 有阻塞项 | 认证/CORS/Safety Guard 待处理 |
| Evaluation | 🟢 闭环 | 38 cases, 100% 通过率（基于当前 case 集） |
| 文档 | 🟢 较完整 | 契约已对齐 |

## 当前活跃会话

| 角色 | 状态 | 当前任务 | 分支 |
|---|---|---|---|
| 架构 Owner | 🟢 活跃 | Phase 3 修复计划制定 | main |
| 全栈开发 (A1) | ✅ 已退出 | 盘点 + 审查完成 | — |
| AI/RAG 工程师 (A2) | ✅ 已退出 | 盘点 + 审查完成 | — |
| QA (A3) | ✅ 已退出 | Smoke Test 完成 | — |
| 安全 Review (A4) | ✅ 已退出 | 安全审查完成 | — |

## 任务进度

| Phase | 总数 | 完成 | 进行中 | 待领取 |
|---|---|---|---|---|
| Phase 1：协作框架 | 3 | 3 | 0 | 0 |
| Phase 2：模块盘点 | 4 | 4 | 0 | 0 |
| Phase 3：审查 | 3 | 3 | 0 | 0 |
| Phase 3：P0 修复 | 5 | 0 | 0 | 5 |
| Phase 3：P1 修复 | 9 | 0 | 0 | 9 |
| Phase 3：P2 优化 | 6 | 0 | 0 | 6 |
| Phase 3：单元测试 | 2 | 0 | 0 | 2 |
| Phase 4：体验优化 | 5 | 0 | 0 | 5 |

## 风险项

| 风险 | 等级 | 说明 |
|---|---|---|
| 无认证授权 | 🔴 高 | 生产化阻塞，所有接口裸奔 |
| CORS 过宽 | 🔴 高 | 允许任意来源 |
| Safety Guard 未覆盖 RAG | 🔴 高 | RAG 链路可绕过安全检查 |
| Python 服务裸露 | 🔴 高 | 端口 8000 无访问控制 |
| 无单元测试 | 🟡 中 | Java 和 Python 都缺 |
| DTO 契约无强类型 | 🟡 中 | Java ↔ Python 手动对齐 |
| 评估集规模小 | 🟡 中 | 38 个 case，覆盖有限 |
| HuggingFace 离线 | 🟡 中 | 国内网络限制 |

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

1. ~~Phase 1 协作文档~~ ✅
2. ~~Phase 2 模块盘点~~ ✅
3. ~~Phase 3 审查（QA + Security）~~ ✅
4. **启动 P1 修复**：FIX-010/011 (.gitignore) + FIX-012 (硬编码) — A1
5. **启动 P0 修复**：FIX-004 (Safety Guard 扩展) — A2
6. 后续：FIX-002 认证方案设计 — A0

> 详见 [phase-3-remediation-plan.md](phase-3-remediation-plan.md)
