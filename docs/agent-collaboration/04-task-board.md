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
| TASK-002 | 修正 architecture.md 描述 | 架构 Owner | main（仅文档） | ✅ | jieba → BM25 描述修正 |
| TASK-003 | 补充 api.md 缺失字段 | 架构 Owner | main（仅文档） | ✅ | keyword_groups、failure_type 字段说明 |

---

## Phase 2：模块盘点（各 Owner 并行）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-010 | 盘点 Java 后端接口和 DTO | 全栈开发 | audit/fullstack-inventory | ✅ | 输出盘点文档，列出 Java ↔ Python 契约差异 |
| TASK-011 | 盘点前端页面能力和不足 | 全栈开发 | audit/fullstack-inventory | ✅ | 输出盘点文档，列出功能缺口 |
| TASK-012 | 盘点 Python AI 各模块状态 | AI/RAG 工程师 | audit/ai-rag-inventory | ✅ | 输出盘点文档，列出各模块状态和风险 |
| TASK-013 | 盘点 Evaluation 体系 | AI/RAG 工程师 | audit/ai-rag-inventory | ✅ | 输出盘点文档，列出 case 覆盖范围和局限 |

---

## Phase 3：质量加固（各 Owner 并行）

### 审查任务（已完成）

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-020 | 制定 Smoke Test 清单 | QA (A3) | audit/qa-smoke | ✅ | 三端启动 + 核心功能验证清单 |
| TASK-021 | 执行 Smoke Test | QA (A3) | audit/qa-smoke | ✅ | 清单全部通过 |
| TASK-022 | 安全审查（API Key + Safety Guard） | 安全 Review (A4) | audit/security-review | ✅ | 输出审查报告，列出风险和建议 |

### P0 修复任务（生产化阻塞项）

| ID | 问题 | 来源 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|---|
| FIX-001 | CORS 配置过宽 | Security | A1 | fix/cors-restrict | ⬜ | 生产配置限制为具体域名 |
| FIX-002 | 无认证/授权机制 | Security | A0+A1 | feat/auth | ⬜ | 至少 API Key 认证 |
| FIX-003 | Python 服务可被直接访问 | Security | A1 | fix/python-access | ⬜ | Python 仅允许 localhost |
| FIX-004 | Safety Guard 仅覆盖 Agent 链路 | Security | A2 | fix/safety-guard-rag | ⬜ | RAG 链路也应用安全检查 |
| FIX-005 | Evaluation 接口无访问限制 | Security | A2 | fix/eval-access | ⬜ | 生产禁用或限角色 |

### P1 修复任务（开发前必须处理）

| ID | 问题 | 来源 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|---|
| FIX-010 | .gitignore 未覆盖 eval reports | QA | A1 | fix/gitignore | ⬜ | eval reports 不出现在 git status |
| FIX-011 | .gitignore 未覆盖 node_modules | QA | A1 | fix/gitignore | ⬜ | node_modules 不出现在 git status |
| FIX-012 | AgentHealthController 硬编码地址 | QA+Security | A1 | fix/health-config | ⬜ | 使用 python.agent.base-url |
| FIX-013 | RestTemplate 无超时配置 | QA+Security | A1 | fix/resttemplate-timeout | ⬜ | 连接 3-5s，读取 30-60s |
| FIX-014 | LLM 调用无重试/超时 | QA | A2 | fix/llm-timeout | ⬜ | 添加 timeout 参数 |
| FIX-015 | 异常信息暴露到响应 | Security | A1+A2 | fix/error-sanitize | ⬜ | 前端仅显示通用错误 |
| FIX-016 | traceId 可被伪造 | Security | A1 | fix/traceid-validate | ⬜ | 验证 UUID 格式 |
| FIX-017 | 无请求大小限制 | Security | A1 | fix/request-size | ⬜ | message 最大 1000 字符 |
| FIX-018 | sources 暴露内部文件名 | Security | A2 | fix/sources-mask | ⬜ | sources 脱敏 |

### P2 修复任务（后续优化）

| ID | 问题 | 来源 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|---|
| FIX-020 | Safety Guard 仅关键词匹配 | Security+QA | A2 | enhance/safety-guard | ⬜ | 补充变体关键词 |
| FIX-021 | 日志打印完整用户问题 | Security | A1+A2 | fix/log-sanitize | ⬜ | 仅打印前 20 字符 |
| FIX-022 | 两个 RAG Prompt 模板不一致 | QA | A2 | fix/prompt-align | ⬜ | 主链路和 Agent Prompt 一致 |
| FIX-023 | RAG Prompt 缺少 Injection 防护 | Security | A2 | fix/prompt-injection | ⬜ | 添加防注入规则 |
| FIX-024 | 无请求频率限制 | Security | A1 | feat/rate-limit | ⬜ | 添加限流 |
| FIX-025 | 日志格式未区分环境 | Security | A1+A2 | fix/log-format | ⬜ | 生产 JSON 格式日志 |

### 单元测试任务

| ID | 任务 | Owner | 分支 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| TASK-023 | 补充 Java 单元测试 | A1 | test/java-unit | ⬜ | Controller 核心逻辑覆盖 |
| TASK-024 | 补充 Python 单元测试 | A2 | test/python-unit | ⬜ | Retrieval + Service 核心逻辑覆盖 |

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

**第一阶段：** Phase 1（协作文档） → ✅ 已完成

**第二阶段：** Phase 2（模块盘点） → ✅ 已完成，A1 + A2 盘点报告已合并

**第三阶段（当前）：** Phase 3（质量加固） → 可启动

**第四阶段：** Phase 4（体验优化） → 可选，根据需要启动
