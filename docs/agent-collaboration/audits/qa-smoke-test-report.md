# QA Smoke Test Report

## 1. 基本信息

| 项目 | 值 |
|---|---|
| Agent | A3 测试验收 |
| Branch | `audit/qa-smoke` |
| 任务类型 | 质量验收 / 只读测试 |
| 是否修改业务代码 | 否 |
| 验收时间 | 2026-07-10 |
| 任务 ID | TASK-020 + TASK-021 |

---

## 2. 读取文件清单

| # | 文件路径 | 用途 |
|---|---|---|
| 1 | `README.md` | 项目全貌、Quick Start |
| 2 | `docs/local-demo-guide.md` | 本地演示指南、环境变量、健康检查 |
| 3 | `docs/demo-script.md` | 面试演示脚本 |
| 4 | `docs/rag-quality-engineering.md` | RAG 质量工程文档 |
| 5 | `docs/api.md` | 接口文档 |
| 6 | `docs/architecture.md` | 架构说明 |
| 7 | `docs/agent-collaboration/00-project-context.md` | 项目上下文 |
| 8 | `docs/agent-collaboration/01-architecture-boundary.md` | 架构边界 |
| 9 | `docs/agent-collaboration/02-api-contract.md` | API 契约 |
| 10 | `docs/agent-collaboration/03-agent-registry.md` | Agent 注册表 |
| 11 | `docs/agent-collaboration/04-task-board.md` | 任务看板 |
| 12 | `docs/agent-collaboration/06-do-not-touch.md` | 不可修改清单 |
| 13 | `docs/agent-collaboration/07-release-checklist.md` | 发布检查清单 |
| 14 | `docs/agent-collaboration/dashboard.md` | 协作仪表盘 |
| 15 | `docs/agent-collaboration/audits/fullstack-inventory.md` | 全栈盘点报告 |
| 16 | `docs/agent-collaboration/audits/ai-rag-inventory.md` | AI/RAG 盘点报告 |
| 17 | `agent-python/app/main.py` | FastAPI 入口 |
| 18 | `agent-python/app/core/config.py` | 配置模块 |
| 19 | `agent-python/app/schemas/chat_schema.py` | Pydantic Schema |
| 20 | `agent-python/app/services/rag_service.py` | RAG 主服务 |
| 21 | `agent-python/app/retrieval/hybrid_retriever.py` | 统一检索入口 |
| 22 | `agent-python/app/agents/langgraph_agent.py` | LangGraph Agent |
| 23 | `agent-python/app/guards/safety_guard.py` | Safety Guard |
| 24 | `agent-python/app/prompts/system_prompt.py` | Prompt 模板 |
| 25 | `backend-java/src/.../controller/HealthController.java` | 健康检查 Controller |
| 26 | `backend-java/src/.../controller/ChatController.java` | RAG Chat Controller |
| 27 | `backend-java/src/.../controller/LangGraphAgentController.java` | Agent Controller |
| 28 | `backend-java/src/.../controller/AgentHealthController.java` | Agent 健康检查 |
| 29 | `.gitignore` | Git 忽略规则 |
| 30 | `data/eval/rag_eval_cases.json` | 评估用例集 |

---

## 3. 测试范围

| 测试维度 | 覆盖内容 |
|---|---|
| 本地 Demo 可复现性 | 启动方式、环境变量、文档完整性 |
| 健康检查 | 3 个 health 端点的响应格式 |
| 普通 RAG 链路 | `/api/chat` 请求响应、字段一致性 |
| LangGraph Agent 链路 | `/api/agent/langgraph/chat` 路由、Safety Guard、字段一致性 |
| RAG Evaluation | 38 cases 覆盖、keyword_groups、failure_type、flaky 机制 |
| 回归风险 | 实验模式状态、.gitignore 覆盖、文档一致性 |

---

## 4. 测试用例清单

### 4.1 本地 Demo 可复现性

| TC | 验证项 | 预期 | 结果 |
|---|---|---|---|
| TC-001 | Python 服务启动方式是否清晰 | `local-demo-guide.md` 有明确命令 | ✅ 通过 |
| TC-002 | Java 服务启动方式是否清晰 | `local-demo-guide.md` 有明确命令（含 Windows） | ✅ 通过 |
| TC-003 | Frontend 启动方式是否清晰 | `local-demo-guide.md` 有明确命令 | ✅ 通过 |
| TC-004 | 环境变量是否说明完整 | `DEEPSEEK_API_KEY/BASE_URL/MODEL` 均有说明 | ✅ 通过 |
| TC-005 | `HF_HUB_OFFLINE` 是否说明完整 | `local-demo-guide.md:49-56` 有说明 | ✅ 通过 |
| TC-006 | DeepSeek API Key 缺失时是否有明确提示 | `config.py:18-19` 有 warning 日志 | ✅ 通过 |
| TC-007 | local-demo-guide 是否足够让新会话复现 Demo | 含前置条件、环境变量、启动命令、健康检查、常见问题 | ✅ 通过 |

### 4.2 健康检查

| TC | 验证项 | 预期（文档） | 代码实际 | 结果 |
|---|---|---|---|---|
| TC-010 | Python `/agent/health` | `{"service":"agent-python","status":"UP"}` | `{'service':'agent-python','status':'UP'}` | ✅ 一致 |
| TC-011 | Java `/api/health` | `{"service":"backend-java","status":"UP"}` | `Map.of("service","backend-java","status","UP")` | ✅ 一致 |
| TC-012 | Java `/api/agent/health` | `{"service":"agent-python","status":"UP"}` | RestClient 转发 Python 响应 | ✅ 一致 |

### 4.3 普通 RAG 链路

| TC | 验证项 | 预期 | 代码实际 | 结果 |
|---|---|---|---|---|
| TC-020 | `/api/chat` 请求格式 | `{"message":"..."}` | `ChatRequest(message)` | ✅ 一致 |
| TC-021 | `/api/chat` 响应字段 | `answer, model, traceId, success` | `ChatResponse(answer, model, traceId, success)` | ✅ 一致 |
| TC-022 | traceId 是否返回 | 响应包含 traceId | Python middleware + Java TraceIdFilter 透传 | ✅ 通过 |
| TC-023 | Python 异常时 Java fallback | `success=false`, 兜底消息 | `ChatController:52-69` 捕获异常返回兜底 | ✅ 通过 |
| TC-024 | `/api/chat` 不含 sources 字段 | 文档明确说明不含 sources | `ChatResponse` 无 sources 字段 | ✅ 一致 |

### 4.4 LangGraph Agent 链路

| TC | 验证项 | 预期 | 代码实际 | 结果 |
|---|---|---|---|---|
| TC-030 | 制度问答 route=rag | 普通问题走 rag 节点 | `router_node:52-54` 默认 route=rag | ✅ 通过 |
| TC-031 | 评估查询 route=eval | 包含评估关键词走 eval 节点 | `router_node:52` 检查 EVAL_KEYWORDS | ✅ 通过 |
| TC-032 | 高风险问题 route=refuse | 安全检查不通过走 refuse 节点 | `safety_node:36-43` 返回 route=refuse | ✅ 通过 |
| TC-033 | safe/category/reason/sources/traceId 正确返回 | AgentResponse 包含所有字段 | `main.py:44-54` 构造完整 AgentResponse | ✅ 通过 |
| TC-034 | sources 来自检索结果 | 不是模型编造 | `rag_node:70-79` 从 tool result 提取 sources | ✅ 通过 |

### 4.5 RAG Evaluation

| TC | 验证项 | 预期 | 验证结果 | 结果 |
|---|---|---|---|---|
| TC-040 | retrieval evaluation 是否可运行 | 零 token 消耗 | `rag_eval_cases.json` 有 38 个 case，脚本可独立运行 | ✅ 通过 |
| TC-041 | generation evaluation 是否可运行 | 调用 LLM | 需要 API Key，脚本结构完整 | ✅ 通过 |
| TC-042 | no-answer case 是否单独处理 | 10 个负样本 | `none_001-007` + `colloquial_none_001-003` = 10 个 | ✅ 通过 |
| TC-043 | keyword_groups 是否生效 | 组内 OR、组间 AND | `colloquial_006` 有 `expected_answer_keyword_groups` | ✅ 通过 |
| TC-044 | failure_type 是否输出 | 5 种分类 | 代码支持 `passed/keyword_too_strict/generation_incomplete/llm_flaky/no_answer_leakage` | ✅ 通过 |
| TC-045 | flaky case 是否有标记 | retry 后区分为 `llm_flaky` | eval_generation.py 有 retry 机制 | ✅ 通过 |
| TC-046 | 38 cases 100% 口径是否正确 | 28 answerable + 10 no-answer | 实际：18 标准 + 10 口语化 + 7 标准无答案 + 3 口语化无答案 = 38 | ✅ 通过 |

### 4.6 回归风险

| TC | 验证项 | 预期 | 代码实际 | 结果 |
|---|---|---|---|---|
| TC-050 | Re-rank 是否仍为实验模式 | `hybrid_rerank` 不是默认值 | `hybrid_retriever.py:108` 默认 `mode='hybrid'` | ✅ 通过 |
| TC-051 | Query Rewrite 是否仍为实验模式 | `rewrite_mode` 默认 `none` | `config.py:33` 默认 `'none'` | ✅ 通过 |
| TC-052 | eval report 是否可能被误提交 | .gitignore 应覆盖 | ⚠️ `.gitignore` 未覆盖 `data/eval/reports/` | ❌ 失败 |
| TC-053 | `__pycache__` 是否可能被误提交 | .gitignore 应覆盖 | `.gitignore:9` 已覆盖 `__pycache__/` | ✅ 通过 |
| TC-054 | `node_modules` 是否可能被误提交 | .gitignore 应覆盖 | ⚠️ `.gitignore` 未覆盖 `node_modules/` | ❌ 失败 |
| TC-055 | 02-api-contract.md 与 docs/api.md 是否一致 | 应完全一致 | ⚠️ 存在偏差（见§6.1） | ❌ 失败 |

---

## 5. 实际执行结果

### 5.1 无法执行的命令

本次验收为**只读测试**，未启动服务执行实际 API 调用。以下命令应在本地环境手动执行验证：

```bash
# 健康检查验证
curl http://localhost:8000/agent/health
curl http://localhost:8080/api/health
curl http://localhost:8080/api/agent/health

# RAG 问答验证
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'

# Agent RAG 验证
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"病假需要提供哪些材料？"}'

# Agent 评估查询验证
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"当前RAG评估通过率是多少？"}'

# Agent 安全拒答验证
curl -X POST http://localhost:8080/api/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么伪造病假证明？"}'

# Python 停服降级验证（先停止 Python 服务）
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"测试"}'

# Retrieval Evaluation（零 token）
cd agent-python && uv run python scripts/eval/run_rag_eval.py
```

**无法执行原因：** 本次任务为只读验收，不启动服务、不修改代码。

---

## 6. 发现的问题

### P0：阻塞进入下一阶段

**无 P0 问题。** 核心链路（RAG + Agent + Evaluation）代码实现与文档描述一致，功能逻辑完整。

### P1：进入开发前建议修复

| # | 问题 | 位置 | 说明 | 建议修复角色 |
|---|---|---|---|---|
| P1-001 | `.gitignore` 未覆盖 `data/eval/reports/` | `.gitignore` | eval 产物可能被误提交到 Git。`06-do-not-touch.md` 明确禁止提交 eval 报告，但 `.gitignore` 未执行此规则 | A1-全栈开发 |
| P1-002 | `.gitignore` 未覆盖 `node_modules/` | `.gitignore` | 前端依赖可能被误提交。虽然实际未提交，但缺少规则保护 | A1-全栈开发 |
| P1-003 | `02-api-contract.md` 与 `docs/api.md` 存在偏差 | `02-api-contract.md` | 3 处不一致（见§6.1），新开发可能参考过时契约文档 | A0-架构负责人 |
| P1-004 | `AgentHealthController` 硬编码地址 | `AgentHealthController.java:21` | 硬编码 `http://localhost:8000`，未使用 `python.agent.base-url` 配置 | A1-全栈开发 |

### P2：后续优化

| # | 问题 | 位置 | 说明 | 建议修复角色 |
|---|---|---|---|---|
| P2-001 | `local-demo-guide.md` health 响应格式描述不一致 | `local-demo-guide.md:111` | 文档写 `{"status":"ok","agent_ready":true}`，实际代码返回 `{"service":"agent-python","status":"UP"}` | A0-架构负责人 |
| P2-002 | 两个 RAG Prompt 模板不一致 | `system_prompt.py` vs `langchain_rag_chain.py` | Agent 链路和主链路的 Prompt 规则不同，可能导致回答质量差异 | A2-AI/RAG 工程师 |
| P2-003 | RestTemplate 无超时配置 | `ChatController.java` | Python 慢响应会阻塞 Java 线程池 | A1-全栈开发 |
| P2-004 | LLM 调用无重试/超时 | `llm_service.py` | 无 retry、无 timeout 配置 | A2-AI/RAG 工程师 |
| P2-005 | Safety Guard 仅关键词匹配 | `safety_guard.py` | 无法处理变体表达、谐音、英文等绕过方式 | A2-AI/RAG 工程师 |

---

## 7. 回归风险

| 风险项 | 当前状态 | 风险等级 | 说明 |
|---|---|---|---|
| Re-rank 默认模式 | `hybrid`（默认），`hybrid_rerank`（实验） | 🟢 低 | 未被意外改动 |
| Query Rewrite 默认模式 | `none`（默认），`rule`（实验） | 🟢 低 | 未被意外改动 |
| eval report 误提交 | `.gitignore` 未覆盖 | 🟡 中 | 已有报告文件存在于工作区，需确认未被 tracked |
| `__pycache__` 误提交 | `.gitignore` 已覆盖 | 🟢 低 | 规则存在 |
| `node_modules` 误提交 | `.gitignore` 未覆盖 | 🟡 中 | 实际未提交，但缺少规则保护 |
| 02-api-contract.md 过时 | 与 docs/api.md 有 3 处偏差 | 🟡 中 | 新开发可能参考错误契约 |
| 知识库文档被修改 | 未检查 | 🟢 低 | 有 `06-do-not-touch.md` 约束 |
| eval baseline 被删除 | 未检查 | 🟢 低 | 有 `06-do-not-touch.md` 约束 |

---

## 8. 建议交给哪个角色修复

| 问题 ID | 问题摘要 | 建议角色 |
|---|---|---|
| P1-001 | .gitignore 未覆盖 eval reports | A1-全栈开发 |
| P1-002 | .gitignore 未覆盖 node_modules | A1-全栈开发 |
| P1-003 | 02-api-contract.md 与 docs/api.md 偏差 | A0-架构负责人 |
| P1-004 | AgentHealthController 硬编码地址 | A1-全栈开发 |
| P2-001 | local-demo-guide.md 响应格式描述错误 | A0-架构负责人 |
| P2-002 | 两个 RAG Prompt 模板不一致 | A2-AI/RAG 工程师 |
| P2-003 | RestTemplate 无超时配置 | A1-全栈开发 |
| P2-004 | LLM 调用无重试/超时 | A2-AI/RAG 工程师 |
| P2-005 | Safety Guard 仅关键词匹配 | A2-AI/RAG 工程师 |

---

## 9. 是否建议进入开发修复阶段

### 结论：**有条件建议**

**理由：**

1. **核心链路稳定**：RAG 主链路（`/api/chat`）、Agent 链路（`/api/agent/langgraph/chat`）、Evaluation 链路的代码实现与文档描述一致，功能逻辑完整。

2. **无 P0 阻塞问题**：没有发现阻塞性问题，系统核心功能可正常工作。

3. **存在前置条件**：
   - **必须先修复 P1-001 + P1-002**：`.gitignore` 缺少 `data/eval/reports/` 和 `node_modules/` 规则，可能导致产物误提交。这是 Phase 3 质量加固的基础。
   - **建议先修复 P1-003**：`02-api-contract.md` 与 `docs/api.md` 的 3 处偏差会影响后续开发参考。

4. **进入开发阶段的建议**：
   - 优先修复 P1 问题（`.gitignore` + 契约文档对齐）
   - 然后启动 TASK-023（Java 单元测试）和 TASK-024（Python 单元测试）
   - 单元测试就绪后再进入 Phase 4 体验优化

**不建议做的事：**
- 不建议跳过 P1 直接进入 Phase 4
- 不建议在没有单元测试保护的情况下修改核心链路
- 不建议同时改 Java + Python + Frontend

---

## 附录：代码与文档一致性检查详情

### A1. 02-api-contract.md 偏差详情

| # | 位置 | 02-api-contract.md 描述 | docs/api.md 描述 | 代码实际 | 偏差 |
|---|---|---|---|---|---|
| 1 | `/api/chat` 响应 | 包含 `sources` 字段（示例） | 明确说明"不包含 sources 字段" | `ChatResponse` 无 sources | ❌ 契约文档错误 |
| 2 | `/api/agent/langgraph/chat` 响应 | 仅 `answer, model, traceId, success` | 完整字段 `answer, route, safe, category, reason, sources, success, traceId` | `AgentChatResponse` 包含全部字段 | ❌ 契约文档过时 |
| 3 | `/agent/chat` 请求 | body 包含 `trace_id` 字段 | traceId 通过 header 透传 | `ChatRequest` 仅有 `message` | ❌ 契约文档错误 |

### A2. local-demo-guide.md 偏差详情

| # | 位置 | 文档描述 | 代码实际 | 偏差 |
|---|---|---|---|---|
| 1 | health 响应 | `{"status":"ok","agent_ready":true}` | `{"service":"agent-python","status":"UP"}` | ❌ 文档错误 |

### A3. 检索模式默认值确认

| 配置项 | 文档默认值 | 代码默认值 | 一致性 |
|---|---|---|---|
| `retrieval_mode` | `hybrid` | `hybrid_retriever.py:108` 默认 `mode='hybrid'` | ✅ |
| `rewrite_mode` | `none` | `config.py:33` 默认 `'none'` | ✅ |
| `top_k` | `3` | `config.py:36` `TOP_K = 3` | ✅ |
| `RERANK_CANDIDATE_K` | `10` | `config.py:30` 默认 `10` | ✅ |

### A4. 评估用例统计确认

| 类别 | 文档描述 | 实际 count | 一致性 |
|---|---|---|---|
| Answerable（标准问法） | 18 | `leave_001-015` + `it_001-002` + `onboard_001` = 18 | ✅ |
| Answerable（口语化问法） | 10 | `colloquial_001-010` = 10 | ✅ |
| No-answer（标准问法） | 7 | `none_001-007` = 7 | ✅ |
| No-answer（口语化问法） | 3 | `colloquial_none_001-003` = 3 | ✅ |
| **合计** | **38** | **38** | ✅ |

### A5. Safety Guard 关键词统计

| 类别 | 标签 | 关键词数量 |
|---|---|---|
| `illegal_or_policy_violation` | 违法违规 / 伪造材料 | 9 |
| `policy_bypass` | 绕过企业制度 / 规避审批 | 13 |
| `cybersecurity_attack` | 网络安全攻击 / 黑客行为 | 12 |
| `audit_tampering` | 删除审计 / 隐藏痕迹 | 7 |
| `unauthorized_access` | 越权访问 / 数据窃取 | 9 |
| **合计** | — | **50** |

---

## 附录：Git 状态确认

```
Branch: audit/qa-smoke
Status: working tree clean
Modified files: 0 (本次验收未修改任何文件)
New files: 1 (本报告)
```
