# 05 - 会话交接模板（Session Handoff Template）

> **每个会话结束时必须填写此模板，存放在 `docs/agent-collaboration/handoff/` 目录。**

## 文件命名

```
docs/agent-collaboration/handoff/[role]-session-handoff-YYYY-MM-DD.md
```

示例：
- `fullstack-session-handoff-2026-07-10.md`
- `ai-rag-session-handoff-2026-07-11.md`

---

## 模板

```markdown
# [角色] Session Handoff

## 基本信息

| 项目 | 值 |
|---|---|
| 会话角色 | （架构 Owner / 全栈开发 / AI/RAG 工程师 / QA / 安全 Review） |
| 分支 | feat/xxx |
| 归档时间 | YYYY-MM-DD HH:MM |
| 下一个会话应读 | 本文档 + 00-project-context.md + 04-task-board.md |

## Git 状态

### 当前分支

（当前在哪个分支，是否 up to date）

### 未提交变更

| 状态 | 文件 | 说明 |
|---|---|---|
| M | xxx | xxx |

### 建议提交内容

- （哪些文件应该提交）
- （哪些文件不应该提交）

## 本次完成的工作

### 已完成任务

| 任务 ID | 任务 | 结果 |
|---|---|---|
| TASK-xxx | xxx | ✅ 完成 / ❌ 未完成 |

### 修改的文件

| 文件 | 修改内容 |
|---|---|
| xxx | xxx |

### 新增的文件

| 文件 | 用途 |
|---|---|
| xxx | xxx |

## 未完成的工作

| 任务 ID | 任务 | 阻塞原因 |
|---|---|---|
| TASK-xxx | xxx | xxx |

## 已知问题

| 问题 | 影响 | 建议 |
|---|---|---|
| xxx | xxx | xxx |

## 给下一个会话的建议

1. （建议 1）
2. （建议 2）
3. （建议 3）

## 当前 Task Board 状态更新

（更新 04-task-board.md 中相关任务的状态）
```

---

## 交接检查清单

会话结束前逐项确认：

- [ ] 所有变更已提交或说明未提交原因
- [ ] Task Board 已更新
- [ ] Agent Registry 已更新状态
- [ ] 交接文档已写入 `handoff/` 目录
- [ ] 没有留下未解决的冲突
- [ ] 没有修改 `06-do-not-touch.md` 中列出的禁止文件
- [ ] eval 结果未被破坏（如涉及 Python 改动）
