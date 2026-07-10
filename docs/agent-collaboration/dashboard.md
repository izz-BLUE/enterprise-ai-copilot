# 协作仪表盘（Collaboration Dashboard）

> **项目总控视图，每日更新。**

## 项目状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 项目定位 | 🟢 Demo 阶段收官 | 本地可复现，已完成生产化准备改造，不做公网部署 |
| 协作框架 | 🟢 已建立 | Phase 1~3 全部完成，A3/A4 复验通过 |
| 代码质量 | 🟢 稳定 | Phase 3 共 12 项修复，A3 回归复验未发现退化 |
| 安全状态 | 🟡 部分修复 | 12 项安全修复已完成；FIX-003 Python 裸露仍为上线前阻塞项 |
| Evaluation | 🟢 闭环 | 38 cases, 100% 通过率（基于当前 case 集） |
| 文档 | 🟢 较完整 | 契约已对齐，部署准备文档已完成 |

## 当前活跃会话

| 角色 | 状态 | 当前任务 | 分支 |
|---|---|---|---|
| 架构 Owner | ✅ Phase 3 收官 | 阶段性总结 + 面试展示收口 | main（仅文档） |
| 全栈开发 (A1) | ✅ Phase 3 完成 | 共完成 8 项修复（Batch 1/2/3-A/3-B） | — |
| AI/RAG 工程师 (A2) | ✅ Phase 3 完成 | 共完成 4 项修复（Batch 1/3-A/3-B） | — |
| QA (A3) | ✅ 复验完成 | Phase 3 回归复验通过 | — |
| 安全 Review (A4) | ✅ 复验完成 | Phase 3 安全复验通过 | — |

## 任务进度

| Phase | 总数 | 完成 | 进行中 | 待领取 |
|---|---|---|---|---|
| Phase 1：协作框架 | 3 | 3 | 0 | 0 |
| Phase 2：模块盘点 | 4 | 4 | 0 | 0 |
| Phase 3：审查 | 3 | 3 | 0 | 0 |
| Phase 3：P0 修复 | 5 | 4 | 0 | 1 |
| Phase 3：P1 修复 | 9 | 8 | 0 | 1 |
| Phase 3：P2 优化 | 6 | 0 | 0 | 6 |
| Phase 3：单元测试 | 2 | 0 | 0 | 2 |
| Phase 4：体验优化 | 5 | 0 | 0 | 5 |

## 风险项

| 风险 | 等级 | 说明 |
|---|---|---|
| 无认证授权 | 🟢 已修复 | Batch 3-B：最小 Admin Token + Evaluation 访问限制（非完整用户体系，admin.token 为空时为 Demo 模式） |
| CORS 过宽 | 🟢 已修复 | Phase 3 Batch 2：改为可配置白名单 |
| Safety Guard 未覆盖 RAG | 🟢 已修复 | Phase 3 Batch 1：RAG 链路已增加 Safety Guard 前置检查 |
| Python 服务裸露 | 🔴 上线前阻塞 | 端口 8000 无访问控制，部署准备说明已完成，上线前必须实施访问控制（FIX-003） |
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
| Batch 3 权限设计 | [phase-3-batch3-access-control-design.md](phase-3-batch3-access-control-design.md) | 权限方案 + 任务拆分 |
| Phase 3 收官总结 | [phase-3-final-summary.md](phase-3-final-summary.md) | 完成内容 + 安全边界 + 面试口径 |
| 部署准备 | [deployment-readiness.md](../deployment-readiness.md) | 环境变量 + 启动流程 + 检查清单 |
| 旧会话交接 | [handoff/legacy-dev-session-handoff.md](handoff/legacy-dev-session-handoff.md) | 了解历史 |

## 下一步

1. ~~Phase 1 协作文档~~ ✅
2. ~~Phase 2 模块盘点~~ ✅
3. ~~Phase 3 审查（QA + Security）~~ ✅
4. ~~Phase 3 Batch 1 修复~~ ✅（FIX-010/011/012 + FIX-004）
5. ~~Phase 3 Batch 2 修复~~ ✅（FIX-001/013/014/017）
6. ~~Phase 3 Batch 3-A 修复~~ ✅（FIX-015 异常收敛 + FIX-016 traceId 验证）
7. ~~Phase 3 Batch 3-B 修复~~ ✅（FIX-002 Admin Token + FIX-005 Eval 访问限制）
8. ~~A5 部署准备~~ ✅（deployment-readiness.md + .env.example + 启动脚本 + 健康检查）
9. ~~A3/A4 复验~~ ✅（QA 回归 + 安全复验均通过）
10. ~~Phase 3 阶段性收官~~ ✅（[phase-3-final-summary.md](phase-3-final-summary.md)）
11. **下一步**：面试演示准备 / 可选 FIX-003 部署边界处理 / P2 优化

> 详见 [phase-3-remediation-plan.md](phase-3-remediation-plan.md) | [phase-3-final-summary.md](phase-3-final-summary.md)
