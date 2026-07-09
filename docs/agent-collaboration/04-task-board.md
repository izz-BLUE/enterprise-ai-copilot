# 04 - 任务看板（Task Board）

> **所有任务必须有 Owner、分支、验收标准。领取任务前先注册会话（见 03-agent-registry.md）。**

## 任务状态

- ⬜ 待领取
- 🔄 进行中
- ✅ 已完成
- ❌ 已取消
- 🚫 已阻塞

---

## Phase 1：协作框架建立（架构 Owner）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-001 | 建立协作文档框架 | 架构 Owner | main（仅文档） | ✅ | 9 个协作文档全部创建 |
| TASK-002 | 修正 architecture.md 描述 | 架构 Owner | fix/arch-doc-jieba | ⬜ | jieba → BM25 描述修正 |
| TASK-003 | 补充 api.md 缺失字段 | AI/RAG 工程师 | docs/api-field-sync | ⬜ | keyword_groups、failure_type 字段说明 |

---

## Phase 2：模块盘点（各 Owner 并行）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-010 | 盘点 Java 后端接口和 DTO | 全栈开发 | — | ⬜ | 输出盘点文档，列出 Java ↔ Python 契约差异 |
| TASK-011 | 盘点前端页面能力和不足 | 全栈开发 | — | ⬜ | 输出盘点文档，列出功能缺口 |
| TASK-012 | 盘点 Python AI 各模块状态 | AI/RAG 工程师 | — | ⬜ | 输出盘点文档，列出各模块状态和风险 |
| TASK-013 | 盘点 Evaluation 体系 | AI/RAG 工程师 | — | ⬜ | 输出盘点文档，列出 case 覆盖范围和局限 |

---

## Phase 3：质量加固（各 Owner 并行）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-020 | 制定 Smoke Test 清单 | QA | — | ⬜ | 三端启动 + 核心功能验证清单 |
| TASK-021 | 执行 Smoke Test | QA | — | ⬜ | 清单全部通过 |
| TASK-022 | 安全审查（API Key + Safety Guard） | 安全 Review | — | ⬜ | 输出审查报告，列出风险和建议 |
| TASK-023 | 补充 Java 单元测试 | 全栈开发 | test/java-unit | ⬜ | Controller 核心逻辑覆盖 |
| TASK-024 | 补充 Python 单元测试 | AI/RAG 工程师 | test/python-unit | ⬜ | Retrieval + Service 核心逻辑覆盖 |

---

## Phase 4：体验优化（可选）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-030 | 前端增加 Loading 状态 | 全栈开发 | feat/frontend-loading | ⬜ | 请求中显示 loading |
| TASK-031 | 前端增加错误边界 | 全栈开发 | feat/frontend-error | ⬜ | 异常时不白屏 |
| TASK-032 | 统一 Java 错误码 | 全栈开发 | feat/java-error-codes | ⬜ | 错误响应格式统一 |
| TASK-033 | 扩充 Eval Cases | AI/RAG 工程师 | feat/expand-eval | ⬜ | 新增 ≥10 个 case，覆盖新场景 |
| TASK-034 | 优化 Query Rewrite 规则 | AI/RAG 工程师 | feat/query-rewrite-opt | ⬜ | 覆盖更多口语化表达 |

---

## 任务规则

1. **领取任务前：** 先注册会话，确认没有其他人正在做
2. **进行中：** 更新状态为 🔄，填写分支名
3. **完成时：** 更新状态为 ✅，提交代码，更新相关文档
4. **阻塞时：** 更新状态为 🚫，说明阻塞原因
5. **取消时：** 更新状态为 ❌，说明取消原因

## 当前优先级

**第一阶段（当前）：** Phase 1（协作文档） → 由架构 Owner 完成

**第二阶段：** Phase 2（模块盘点） → 各 Owner 并行启动

**第三阶段：** Phase 3（质量加固） → 盘点完成后启动

**第四阶段：** Phase 4（体验优化） → 可选，根据需要启动
