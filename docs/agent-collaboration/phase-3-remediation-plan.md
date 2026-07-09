# Phase 3 质量加固修复计划

> 基于 A3 QA Smoke Test、A4 Security Review、A1 全栈盘点、A2 AI/RAG 盘点报告汇总。

## P0：生产化阻塞项

> 如果项目要声明"可部署公网"，以下问题必须先解决。当前 Demo 阶段风险可控。

| Task ID | 问题 | 来源 | Owner | 分支 | 修复范围 | 验收标准 | 阻塞生产化 | 状态 |
|---|---|---|---|---|---|---|---|---|
| FIX-001 | CORS 配置过宽：`allowedOriginPatterns("*")` | Security P0-1 | A1 | fix/fullstack-phase3-batch2 | `WebConfig.java` + `application.properties` | 可配置白名单 `cors.allowed-origins` | ✅ 是 | ✅ |
| FIX-002 | 无认证/授权机制 | Security P0-2 | A0 设计 + A1 实现 | feat/auth | Java Filter/Controller | 至少实现 API Key 认证，评估接口限角色 | ✅ 是 | ⬜ |
| FIX-003 | Python 服务可被直接访问（端口 8000 无控制） | Security P0-3 | A1 | fix/python-access | `application.properties` + 部署文档 | Python 仅允许 localhost 访问，或添加内部 API Key | ✅ 是 | ⬜ |
| FIX-004 | Safety Guard 仅覆盖 Agent 链路，RAG 主链路无安全检查 | Security P0-4 | A2 | fix/safety-guard-rag | `rag_service.py` | RAG 主链路也应用 Safety Guard 检查 | ✅ 是 | ✅ |
| FIX-005 | Evaluation 接口无访问限制，任何用户可查询评估报告 | Security P0-5 | A2 | fix/eval-access | `langgraph_agent.py` | 生产环境禁用 eval_node 或添加角色判断 | ✅ 是 | ⬜ |

---

## P1：进入开发修复前必须处理

> 不阻塞 Demo，但阻塞后续开发质量和生产化准备。

| Task ID | 问题 | 来源 | Owner | 分支 | 修复范围 | 验收标准 | 阻塞生产化 | 状态 |
|---|---|---|---|---|---|---|---|---|
| FIX-010 | `.gitignore` 未覆盖 `data/eval/reports/` | QA P1-001 | A1 | fix/gitignore | `.gitignore` | eval reports 不出现在 `git status` | ❌ | ✅ |
| FIX-011 | `.gitignore` 未覆盖 `node_modules/` | QA P1-002 | A1 | fix/gitignore | `.gitignore` | node_modules 不出现在 `git status` | ❌ | ✅ |
| FIX-012 | `AgentHealthController` 硬编码 `http://localhost:8000` | QA P1-004, Security P2-4 | A1 | fix/health-config | `AgentHealthController.java` | 使用 `python.agent.base-url` 配置 | ❌ | ✅ |
| FIX-013 | RestTemplate 无超时配置 | QA P2-003, Security P1-4 | A1 | fix/fullstack-phase3-batch2 | `RestClientConfig.java` + `application.properties` | 连接 3s，读取 30s，可配置 | ❌ | ✅ |
| FIX-014 | LLM 调用无重试/超时配置 | QA P2-004 | A2 | fix/ai-phase3-batch2 | `llm_service.py` + `config.py` | LLM_TIMEOUT 默认 30s，超时返回错误 | ❌ | ✅ |
| FIX-015 | 异常信息暴露到响应（`reason=str(e)`） | Security P1-2 | A1 + A2 | fix/error-sanitize | `main.py` + Java Controller | 前端仅显示通用错误，详情记日志 | ❌ | ⬜ |
| FIX-016 | traceId 可被伪造，未验证格式 | Security P1-5 | A1 | fix/traceid-validate | `TraceIdFilter.java` | 验证 UUID 格式，拒绝非法输入 | ❌ | ⬜ |
| FIX-017 | 无请求大小限制，message 字段无长度校验 | Security P1-6 | A1 + A2 | fix/fullstack-phase3-batch2 + fix/ai-phase3-batch2 | `ChatRequest.java` + `main.py` | Java @Size(max=2000) + Python MAX_MESSAGE_LENGTH 兜底 | ❌ | ✅ |
| FIX-018 | sources 字段暴露内部文件名 | Security P1-7 | A2 | fix/sources-mask | `rag_service.py` | sources 仅展示文档标题或脱敏后的名称 | ❌ | ⬜ |

---

## P2：后续优化

> 不阻塞任何阶段，作为持续改进项。

| Task ID | 问题 | 来源 | Owner | 分支 | 修复范围 | 验收标准 | 阻塞生产化 | 状态 |
|---|---|---|---|---|---|---|---|---|
| FIX-020 | Safety Guard 仅关键词匹配，无法防变体绕过 | Security P1-3, QA P2-005 | A2 | enhance/safety-guard | `safety_guard.py` | 补充同音字、拼音、英文变体关键词 | ❌ | ⬜ |
| FIX-021 | 日志打印完整用户问题，无脱敏 | Security P1-1 | A1 + A2 | fix/log-sanitize | Java Controller + Python main.py | 日志仅打印问题前 20 字符 | ❌ | ⬜ |
| FIX-022 | 两个 RAG Prompt 模板不一致 | QA P2-002 | A2 | fix/prompt-align | `system_prompt.py` + `langchain_rag_chain.py` | 主链路和 Agent 链路 Prompt 规则一致 | ❌ | ⬜ |
| FIX-023 | RAG Prompt 缺少 Prompt Injection 防护规则 | Security §8.2 | A2 | fix/prompt-injection | `system_prompt.py` | 添加"禁止忽略系统提示词"等规则 | ❌ | ⬜ |
| FIX-024 | 无请求频率限制 | Security P2-3 | A1 | feat/rate-limit | Java 层 | 生产环境添加限流（如 100 次/分钟） | ❌ | ⬜ |
| FIX-025 | 日志格式未区分环境 | Security P2-2 | A1 + A2 | fix/log-format | Java + Python 配置 | 生产环境 JSON 格式日志 | ❌ | ⬜ |

---

## 任务依赖关系

```
FIX-010 + FIX-011（.gitignore）
  ↓ 无依赖，可立即启动
FIX-012（AgentHealthController 硬编码）
  ↓ 无依赖，可立即启动
FIX-013（RestTemplate 超时）
  ↓ 无依赖，可立即启动
FIX-001（CORS 收紧）
  ↓ 依赖 FIX-002（认证方案确定后才能决定 CORS 策略）
FIX-002（认证授权）
  ↓ 是 FIX-001、FIX-005 的前置
FIX-004（Safety Guard 扩展到 RAG）
  ↓ 无依赖，可立即启动
FIX-005（Eval 访问限制）
  ↓ 依赖 FIX-002（需要角色判断机制）
FIX-003（Python 访问控制）
  ↓ 无依赖，可立即启动
```

## 推荐执行顺序

**第一批（立即启动，无依赖）：**
- FIX-010 + FIX-011：`.gitignore` 修复（A1，5 分钟）
- FIX-012：AgentHealthController 硬编码修复（A1，10 分钟）
- FIX-004：Safety Guard 扩展到 RAG 主链路（A2，30 分钟）

**第二批（第一批完成后）：**
- FIX-013：RestTemplate 超时配置（A1）
- FIX-014：LLM 调用超时配置（A2）
- FIX-015：异常信息脱敏（A1 + A2）
- FIX-016：traceId 格式验证（A1）
- FIX-017：请求大小限制（A1）

**第三批（需要架构设计）：**
- FIX-002：认证授权方案（A0 设计 → A1 实现）
- FIX-001：CORS 收紧（依赖 FIX-002）
- FIX-003：Python 访问控制（A1）
- FIX-005：Eval 访问限制（依赖 FIX-002）

**第四批（持续优化）：**
- FIX-018 ~ FIX-025
