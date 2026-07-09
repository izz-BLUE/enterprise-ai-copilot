# Security Phase 3 Regression Report

## 1. 基本信息

| 项目 | 值 |
|---|---|
| Agent | A4 安全审查 |
| Branch | audit/security-phase3-regression |
| 任务类型 | Phase 3 安全复验 |
| 是否修改业务代码 | 否 |
| 复验时间 | 2026-07-10 |
| 任务 ID | Phase 3 Batch 1 / 2 / 3-A / 3-B 安全复验 |

---

## 2. 读取文件清单

| # | 文件路径 | 用途 |
|---|---|---|
| 1 | `README.md` | 项目全貌、admin.token 说明 |
| 2 | `docs/api.md` | 接口文档、权限行为说明 |
| 3 | `docs/architecture.md` | 架构说明、Phase 3 变更 |
| 4 | `docs/agent-collaboration/phase-3-remediation-plan.md` | 修复计划 |
| 5 | `docs/agent-collaboration/phase-3-batch3-access-control-design.md` | Batch 3 权限设计 |
| 6 | `docs/agent-collaboration/04-task-board.md` | 任务看板 |
| 7 | `docs/agent-collaboration/dashboard.md` | 协作仪表盘 |
| 8 | `docs/agent-collaboration/audits/security-review-report.md` | 初次安全审查报告 |
| 9 | `backend-java/.../controller/LangGraphAgentController.java` | Admin Token + X-Allow-Eval |
| 10 | `backend-java/.../filter/TraceIdFilter.java` | traceId 验证 |
| 11 | `backend-java/.../config/WebConfig.java` | CORS 配置 |
| 12 | `backend-java/.../config/RestClientConfig.java` | RestTemplate 超时 |
| 13 | `backend-java/.../controller/GlobalExceptionHandler.java` | 全局异常处理 |
| 14 | `agent-python/app/main.py` | FastAPI 入口、X-Allow-Eval 读取 |
| 15 | `agent-python/app/agents/langgraph_agent.py` | router_node eval 控制 |
| 16 | `agent-python/app/services/rag_service.py` | Safety Guard 前置检查 |
| 17 | `agent-python/app/services/llm_service.py` | LLM 超时配置 |
| 18 | `agent-python/app/core/config.py` | 配置项 |
| 19 | `.gitignore` | Git 忽略规则 |

---

## 3. 已修复项复验

### Batch 1 修复项

| FIX ID | 问题 | 状态 | 复验结论 |
|---|---|---|---|
| FIX-010 | `.gitignore` 未覆盖 eval reports | ✅ 已修复 | `.gitignore:35` 已添加 `data/eval/reports/` |
| FIX-011 | `.gitignore` 未覆盖 node_modules | ✅ 已修复 | `.gitignore:38` 已添加 `node_modules/` |
| FIX-012 | AgentHealthController 硬编码地址 | ✅ 已修复 | 从 dashboard 确认已完成 |
| FIX-004 | Safety Guard 仅覆盖 Agent 链路 | ✅ 已修复 | `rag_service.py:22-31` 已添加 Safety Guard 前置检查 |

**复验结论：Batch 1 全部修复有效。**

---

### Batch 2 修复项

| FIX ID | 问题 | 状态 | 复验结论 |
|---|---|---|---|
| FIX-001 | CORS 配置过宽 | ✅ 已修复 | `WebConfig.java:11` 使用 `allowedOrigins(allowedOrigins)` 配置白名单，默认 `http://localhost:5173,http://127.0.0.1:5173` |
| FIX-013 | RestTemplate 无超时配置 | ✅ 已修复 | `RestClientConfig.java:13-14` 配置连接超时 3000ms、读取超时 30000ms，可配置覆盖 |
| FIX-014 | LLM 调用无超时 | ✅ 已修复 | `llm_service.py:19` 配置 timeout，`config.py:36` LLM_TIMEOUT 默认 30s，超时抛出 APITimeoutError |
| FIX-017 | 无请求大小限制 | ✅ 已修复 | Java `@Valid @RequestBody` + `@Size(max=2000)` 校验；Python `main.py:32-34` + `main.py:60-72` MAX_MESSAGE_LENGTH 兜底校验 |

**复验结论：Batch 2 全部修复有效。**

---

### Batch 3-A 修复项

| FIX ID | 问题 | 状态 | 复验结论 |
|---|---|---|---|
| FIX-016 | traceId 可被伪造 | ✅ 已修复 | `TraceIdFilter.java:32` 统一生成 UUID，**完全不读取客户端传入的 X-Trace-Id**，杜绝日志注入风险 |
| FIX-015 | 异常信息暴露到响应 | ✅ 已修复 | Java `LangGraphAgentController.java:88-98` fallback 方法 reason 为空字符串；Python `main.py:90-99` reason='' 不暴露 str(e) |

**复验结论：Batch 3-A 全部修复有效。**

---

### Batch 3-B 修复项

| FIX ID | 问题 | 状态 | 复验结论 |
|---|---|---|---|
| FIX-002 | 无认证/授权机制 | ✅ 已修复 | `LangGraphAgentController.java:32-53` 实现 admin.token + X-Admin-Token 校验 + X-Allow-Eval 传递 |
| FIX-005 | Evaluation 接口无访问限制 | ✅ 已修复 | `langgraph_agent.py:53-61` router_node 根据 allow_eval 控制 eval 路由；无权限时返回 access_control 拒答 |

**复验结论：Batch 3-B 全部修复有效。**

---

## 4. 仍存在的 P0 问题

### FIX-003: Python 服务可被直接访问 ⬜ 未修复

**问题：** Python FastAPI 服务（端口 8000）无任何访问控制，可被直接访问。

**风险验证：**

```bash
# 直接访问 Python 服务，伪造 X-Allow-Eval=true
curl -X POST http://localhost:8000/agent/langgraph/chat \
  -H "Content-Type: application/json" \
  -H "X-Allow-Eval: true" \
  -d '{"message":"当前RAG评估通过率是多少？"}'
# 预期：可直接访问 eval 能力，绕过 Java 层所有安全检查
```

**影响：**
- 绕过 Java 层的 Admin Token 校验
- 绕过 Safety Guard（RAG 主链路）
- 直接访问 LLM 调用能力
- 伪造 X-Allow-Eval=true 直接访问 eval 能力

**结论：**
- 这是当前唯一剩余的 P0 生产化阻塞项
- 属于部署层 / DevOps 范畴，需要网络层或应用层访问控制
- 当前**不能声明生产安全完成**

---

## 5. P1 / P2 风险

### P1 风险（不阻塞 Demo，阻塞生产化）

| FIX ID | 问题 | 状态 | 说明 |
|---|---|---|---|
| FIX-018 | sources 暴露内部文件名 | ⬜ 未修复 | sources 仍显示 chunk ID（如 `hr_leave_policy_real_sample_010`） |
| FIX-020 | Safety Guard 仅关键词匹配 | ⬜ 未修复 | 无法防变体绕过（同音字、拼音、英文） |
| FIX-021 | 日志打印完整用户问题 | ⬜ 未修复 | `rag_service.py:44` 仍打印完整用户问题 |
| FIX-022 | 两个 RAG Prompt 模板不一致 | ⬜ 未修复 | `system_prompt.py` 与 `langchain_rag_chain.py` 规则不同 |
| FIX-023 | RAG Prompt 缺少 Injection 防护 | ⬜ 未修复 | 未添加"禁止忽略系统提示词"等规则 |
| FIX-024 | 无请求频率限制 | ⬜ 未修复 | 无任何限流机制 |
| FIX-025 | 日志格式未区分环境 | ⬜ 未修复 | 使用统一 INFO 级别 |

---

## 6. Admin Token 结论

### 6.1 设计评估

**结论：设计合理，符合最小可解释方案。**

| 检查项 | 状态 | 说明 |
|---|---|---|
| admin.token 是否默认空 | ✅ 是 | `@Value("${admin.token:}")` 默认空字符串 |
| admin.token 为空是否明确只是 Demo 模式 | ✅ 是 | 代码注释 + 文档明确说明"Demo 模式，零配置允许 eval" |
| admin.token 非空时是否校验 X-Admin-Token | ✅ 是 | `isEvalAllowed()` 方法校验 token 匹配 |
| 是否打印或返回 admin.token | ✅ 否 | 日志仅打印 `allowEval={}`，不打印 token 值 |
| 是否引入了不必要的登录系统 | ✅ 否 | 未引入 Spring Security / JWT / Session |
| 是否错误信任前端 role | ✅ 否 | 权限判断在 Java 后端完成，不信任前端 |

### 6.2 安全边界声明

文档已明确声明：
- `admin.token` 为空时属于**本地 Demo 便捷模式**
- 该模式**不具备生产安全性**
- 生产化部署**必须**配置 `admin.token`，或替换为正式认证体系
- 当前方案是**最小 Admin Token + Evaluation 访问限制**，不是完整用户权限体系

---

## 7. Evaluation 访问限制结论

### 7.1 实现验证

**结论：实现正确，符合设计预期。**

| 检查项 | 状态 | 说明 |
|---|---|---|
| Java 是否只向 Python 传递 X-Allow-Eval | ✅ 是 | `LangGraphAgentController.java:66` 设置 `X-Allow-Eval` header |
| Python 是否在 router_node 控制 eval route | ✅ 是 | `langgraph_agent.py:53-61` 根据 `allow_eval` 控制 |
| allow_eval=false 是否阻止 eval_node | ✅ 是 | 返回 `route=refuse, category=access_control` |
| 无权限时是否不泄露 eval report | ✅ 是 | 返回通用拒答文案，不返回评估数据 |
| allow_eval=true 是否能正常进入 eval_node | ✅ 是 | Demo 模式下正常返回评估报告 |

### 7.2 权限链路验证

```
用户请求 → Java LangGraphAgentController
  → 检查 admin.token / X-Admin-Token
  → 设置 X-Allow-Eval: true/false
  → Python /agent/langgraph/chat
    → router_node 根据 allow_eval 控制
    → allow_eval=false + eval 关键词 → route=refuse (access_control)
    → allow_eval=true + eval 关键词 → route=eval
```

---

## 8. X-Allow-Eval 边界结论

### 8.1 安全边界声明

**结论：文档已明确声明 X-Allow-Eval 不是认证凭证。**

从 `phase-3-batch3-access-control-design.md:171-178`：

> - `X-Allow-Eval` **不是认证凭证**，不代表任何用户身份或独立安全判断。
> - `X-Allow-Eval` 只表示 Java 后端已完成管理员权限判断，允许 Python 执行 eval 路由。
> - Python **不应**将 `X-Allow-Eval` 当作独立安全边界 — 它只是 Java 决策的传递信号。
> - 如果 Python 服务被外部直接访问（绕过 Java），攻击者可伪造 `X-Allow-Eval: true` 请求头，直接访问 eval 能力。
> - 因此，Python 服务直接暴露仍属于 **FIX-003** 的范围。

### 8.2 当前风险

- Python 服务直接暴露时，攻击者可伪造 `X-Allow-Eval: true`
- 这是 FIX-003 的范围，尚未解决
- 当前方案是**最小 Admin Token + Evaluation 访问限制**，不等价于解决 FIX-003

---

## 9. FIX-003 Python 服务裸露结论

### 9.1 当前状态

**结论：FIX-003 仍属于 P0 生产化阻塞项，尚未修复。**

| 检查项 | 状态 | 说明 |
|---|---|---|
| 直接访问 Python :8000 时，是否仍可伪造 X-Allow-Eval=true | ✅ 是 | Python 读取 header，无独立验证 |
| 这是否仍属于 FIX-003 | ✅ 是 | 设计文档明确归类为 FIX-003 |
| 当前是否不能声明生产级安全完成 | ✅ 是 | Python 裸露是唯一剩余 P0 |
| 后续应由 DevOps / 部署边界解决 | ✅ 是 | 需要网络层或应用层访问控制 |

### 9.2 建议解决方案

**方案 A：部署层限制（推荐）**
- Python 服务绑定 `127.0.0.1:8000`，仅允许本地访问
- 通过防火墙 / iptables 限制端口访问来源
- 通过反向代理（Nginx）限制访问

**方案 B：应用层兜底**
- Python 服务添加内部 API Key 认证
- Java 调用 Python 时携带内部 API Key
- Python 验证内部 API Key 后才处理请求

**方案 C：两者结合**
- 部署层限制 + 应用层兜底
- 纵深防御，最安全

---

## 10. 是否建议进入下一阶段

### 10.1 是否可以继续本地 Demo / 面试演示

**结论：可以。**

**理由：**
- 本地运行时 Python 服务不暴露给外部网络
- admin.token 为空时所有功能可用，不影响演示
- Safety Guard 已覆盖 RAG 主链路
- 异常信息已收敛，不暴露内部细节
- traceId 已统一生成，无注入风险

**建议：**
- 面试演示时说明"当前为本地 Demo，Python 服务仅本地访问"
- 不要声称"已部署生产"或"生产安全完成"

---

### 10.2 是否可以声明生产安全完成

**结论：不可以。**

**理由：**
- FIX-003（Python 服务裸露）尚未修复
- admin.token 为空时无真实认证
- 无请求频率限制
- 无用户身份追踪和审计日志
- Safety Guard 仅关键词匹配，可被变体绕过
- RAG Prompt 缺少 Injection 防护规则

**必须在生产化前完成：**
1. FIX-003：Python 服务访问控制（部署层）
2. admin.token 配置为非空值
3. 或替换为正式认证体系（JWT + 用户体系）

---

### 10.3 是否建议进入 FIX-003

**结论：建议。**

**理由：**
- FIX-003 是当前唯一剩余的 P0 生产化阻塞项
- 修复后可声明"代码层安全基本完成"
- 剩余 P1/P2 项可后续迭代处理

**建议执行顺序：**
1. **FIX-003**：Python 服务绑定 localhost（DevOps）
2. **FIX-018**：sources 脱敏（A2）
3. **FIX-021**：日志脱敏（A1 + A2）
4. **FIX-023**：RAG Prompt Injection 防护（A2）

---

## 附录：Phase 3 安全修复验证清单

| 检查项 | Batch 1 | Batch 2 | Batch 3-A | Batch 3-B | 状态 |
|---|---|---|---|---|---|
| CORS 白名单 | — | ✅ | — | — | 已修复 |
| RestTemplate 超时 | — | ✅ | — | — | 已修复 |
| LLM 超时 | — | ✅ | — | — | 已修复 |
| 输入长度校验 | — | ✅ | — | — | 已修复 |
| Safety Guard 覆盖 RAG | ✅ | — | — | — | 已修复 |
| .gitignore 覆盖 | ✅ | — | — | — | 已修复 |
| traceId 验证 | — | — | ✅ | — | 已修复 |
| 异常信息收敛 | — | — | ✅ | — | 已修复 |
| Admin Token | — | — | — | ✅ | 已修复 |
| Eval 访问限制 | — | — | — | ✅ | 已修复 |
| Python 访问控制 | ⬜ | ⬜ | ⬜ | ⬜ | **未修复** |

---

**复验完成时间：** 2026-07-10
**复验人：** A4 安全审查 Agent
**分支：** audit/security-phase3-regression
