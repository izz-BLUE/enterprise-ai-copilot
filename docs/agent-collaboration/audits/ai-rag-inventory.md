# AI/RAG Inventory Report

## 1. 基本信息

| 项目 | 值 |
|---|---|
| Agent | A2 AI/RAG Engineer |
| Branch | audit/ai-rag-inventory |
| 任务类型 | 只读盘点 |
| 是否修改业务逻辑 | 否 |
| 盘点时间 | 2026-07-10 |
| 任务 ID | TASK-012 + TASK-013 |

---

## 2. Python FastAPI 服务盘点

### 2.1 入口文件

| 项目 | 值 |
|---|---|
| 入口文件 | `agent-python/app/main.py` |
| 框架 | FastAPI + Uvicorn |
| 端口 | 8000 |
| 中间件 | `trace_id_middleware`（HTTP 中间件，处理 traceId 透传） |

### 2.2 API 端点清单

| Method | Path | 处理函数 | 返回类型 | 状态 |
|---|---|---|---|---|
| GET | `/agent/health` | `health()` | dict | ✅ 稳定 |
| POST | `/agent/chat` | `chat()` → `process_chat()` | `ChatResponse` | ✅ 稳定主链路 |
| POST | `/agent/langgraph/chat` | `langgraph_chat()` → `run_langgraph_agent()` | `AgentResponse` | 🧪 实验链路 |

### 2.3 Pydantic Schema

| Schema | 字段 | 位置 |
|---|---|---|
| `ChatRequest` | `message: str` | `app/schemas/chat_schema.py` |
| `ChatResponse` | `answer, model, traceId, success` | `app/schemas/chat_schema.py` |
| `AgentResponse` | `answer, route, safe, category, reason, sources, success, traceId` | `app/schemas/chat_schema.py` |

### 2.4 配置项

| 配置 | 环境变量 | 默认值 | 位置 |
|---|---|---|---|
| LLM API Key | `DEEPSEEK_API_KEY` | 无（必填） | `app/core/config.py` |
| LLM Base URL | `DEEPSEEK_BASE_URL` | 无 | `app/core/config.py` |
| LLM Model | `DEEPSEEK_MODEL` | 无 | `app/core/config.py` |
| LLM Temperature | `DEEPSEEK_TEMPERATURE` | `0` | `app/core/config.py` |
| Re-rank 模型 | `RERANK_MODEL` | `BAAI/bge-reranker-base` | `app/core/config.py` |
| Re-rank 候选数 | `RERANK_CANDIDATE_K` | `10` | `app/core/config.py` |
| Query Rewrite 模式 | `REWRITE_MODE` | `none` | `app/core/config.py` |

### 2.5 模块清单

```
agent-python/app/
├── main.py                  # FastAPI 入口 + trace_id_middleware
├── core/config.py           # 环境变量、路径、常量
├── schemas/chat_schema.py   # Pydantic 请求/响应模型
├── services/
│   ├── rag_service.py       # RAG 主服务（检索 → Prompt → LLM）
│   └── llm_service.py       # LLM 调用封装（OpenAI SDK）
├── retrieval/
│   ├── faiss_retriever.py   # Faiss 向量检索
│   ├── keyword_retriever.py # 简单关键词检索（vector 模式用）
│   ├── bm25_retriever.py    # BM25 检索（字符级 n-gram）
│   ├── hybrid_retriever.py  # 统一检索入口（vector/hybrid/hybrid_rerank）
│   ├── query_rewriter.py    # 规则版 Query Rewrite（实验）
│   └── cross_encoder_reranker.py # Cross Encoder 精排（实验）
├── prompts/system_prompt.py # System Prompt + build_rag_prompt()
├── chains/langchain_rag_chain.py # LangChain RAG Chain（实验）
├── tools/rag_tools.py       # LangChain @tool 封装
├── agents/langgraph_agent.py # LangGraph Agent 状态图
└── guards/safety_guard.py   # Safety Guard（规则版）
```

---

## 3. RAG 链路盘点

### 3.1 稳定主链路（`/agent/chat`）

```
ChatRequest.message
  → rag_service.process_chat()
    → query_rewriter.rewrite_query()          # 实验模式，none 时跳过
    → hybrid_retriever.retrieve()             # 默认 hybrid 模式
      ├── faiss_retriever.retrieve()          # BGE embedding 语义检索
      └── bm25_retriever.retrieve()           # 字符级 n-gram BM25
    → _rrf_fusion()                           # RRF 融合排序 → TopK=3
    → build_rag_prompt()                      # 拼接 RAG Prompt
    → llm_service.call_llm()                  # OpenAI SDK → DeepSeek API
    → ChatResponse
```

**状态：** ✅ 稳定。手写全链路，不依赖 LangChain/LangGraph。

### 3.2 三种检索模式

| 模式 | 入口函数 | 组合方式 | 默认 | 状态 |
|---|---|---|---|---|
| `vector` | `retrieve_vector()` | Faiss + keyword 合并去重 | 否 | ✅ 可用 |
| `hybrid` | `retrieve_hybrid()` | Faiss + BM25 + RRF 融合 | **是** | ✅ 默认 |
| `hybrid_rerank` | `retrieve_hybrid_rerank()` | Hybrid → Top10 候选 → Cross Encoder 精排 | 否 | 🧪 实验 |

统一入口：`hybrid_retriever.retrieve(query, top_k, mode)` — `app/retrieval/hybrid_retriever.py:108`

### 3.3 BM25 实现

| 项目 | 值 |
|---|---|
| 位置 | `app/retrieval/bm25_retriever.py` |
| 分词方式 | 字符级 n-gram（2-gram + 3-gram），无外部依赖 |
| 参数 | K1=1.5, B=0.75 |
| 停用词 | 内置中文停用词表（~80 词） |
| 索引构建 | 模块加载时自动从 `chunks.json` 构建 |
| 状态 | ✅ 可用，对中文友好 |

### 3.4 RRF 实现

| 项目 | 值 |
|---|---|
| 位置 | `app/retrieval/hybrid_retriever.py:20`（`_rrf_fusion` 函数） |
| 算法 | Reciprocal Rank Fusion: `score += 1 / (K + rank)` |
| 常数 | RRF_K = 60 |
| 输入 | 多个排序列表（Faiss + BM25） |
| 输出 | 融合后 TopK |
| 状态 | ✅ 可用 |

### 3.5 Query Rewrite 实验模式

| 项目 | 值 |
|---|---|
| 位置 | `app/retrieval/query_rewriter.py` |
| 模式 | `none`（默认）/ `rule`（实验） |
| 实现方式 | 正则表达式匹配，不调用 LLM |
| 规则数量 | 9 条规则（病假、工作时间、VPN、年假、请假、入职、离职、审批） |
| 设计要点 | 只改写检索用 query，不改 prompt 中的 original_query |
| 默认状态 | ❌ 默认不启用（`REWRITE_MODE=none`） |
| 状态 | 🧪 实验模式 |

### 3.6 Cross Encoder Re-rank 实验模式

| 项目 | 值 |
|---|---|
| 位置 | `app/retrieval/cross_encoder_reranker.py` |
| 模型 | `BAAI/bge-reranker-base`（sentence-transformers CrossEncoder） |
| 候选数 | `RERANK_CANDIDATE_K=10`（环境变量可配） |
| 加载方式 | 延迟加载，全局单例 |
| 降级策略 | 模型不可用时返回原始排序前 TopK |
| 默认状态 | ❌ 默认不启用（`RETRIEVAL_MODE=hybrid`） |
| 状态 | 🧪 实验模式，当前评估集提升不显著 |

### 3.7 Prompt 构造

| 项目 | 值 |
|---|---|
| System Prompt | `app/prompts/system_prompt.py:1`（`SYSTEM_PROMPT` 常量） |
| RAG Prompt | `app/prompts/system_prompt.py:14`（`build_rag_prompt()` 函数） |
| 无知识命中时 | 明确指示"当前知识库暂无相关信息"，不编造 |
| 完整性规则 | 时间范围完整、制度原词保留、材料清单逐条列出、多来源差异标注 |

---

## 4. Agent / Safety 盘点

### 4.1 LangGraph Agent 节点清单

| 节点 | 函数 | 位置 | 职责 |
|---|---|---|---|
| `safety_node` | `safety_node()` | `langgraph_agent.py:32` | 输入安全检查 |
| `router_node` | `router_node()` | `langgraph_agent.py:47` | 意图路由（规则匹配） |
| `rag_node` | `rag_node()` | `langgraph_agent.py:57` | RAG 问答（调用 `rag_answer_tool`） |
| `eval_node` | `eval_node()` | `langgraph_agent.py:82` | 评估查询（调用 `eval_report_tool`） |
| `refuse_node` | `refuse_node()` | `langgraph_agent.py:112` | 安全拒答 |

**状态图：**
```
START → safety_node → router_node → { rag_node | eval_node | refuse_node } → END
```

**路由规则：**
- 包含评估关键词（`评估, 通过率, pass_rate, 命中率, baseline, 回归, flaky`）→ `eval`
- 安全检查不通过 → `refuse`
- 其他 → `rag`

### 4.2 Safety Guard 实现

| 项目 | 值 |
|---|---|
| 位置 | `app/guards/safety_guard.py` |
| 实现方式 | 规则版关键词匹配，不调用 LLM |
| 风险类别 | 5 类（见下表） |
| 空查询处理 | 返回 `safe=False, category=empty_query` |

| 类别 | 标签 | 关键词数量 |
|---|---|---|
| `illegal_or_policy_violation` | 违法违规 / 伪造材料 | 9 |
| `policy_bypass` | 绕过企业制度 / 规避审批 | 13 |
| `cybersecurity_attack` | 网络安全攻击 / 黑客行为 | 12 |
| `audit_tampering` | 删除审计 / 隐藏痕迹 | 7 |
| `unauthorized_access` | 越权访问 / 数据窃取 | 9 |

**状态：** ✅ 可用。仅覆盖 Agent 链路（`/agent/langgraph/chat`），不影响 RAG 主链路（`/agent/chat`）。

### 4.3 Tools 清单

| Tool | 位置 | 职责 |
|---|---|---|
| `rag_answer_tool` | `app/tools/rag_tools.py:22` | RAG 问答（调用 LangChain RAG Chain） |
| `eval_report_tool` | `app/tools/rag_tools.py:41` | 读取评估报告 JSON |

**注意：** `rag_answer_tool` 内部调用的是 `langchain_rag_chain.answer_with_langchain_rag()`，不是 `rag_service.process_chat()`。两条链路的 Prompt 模板略有差异（LangChain 版较简洁）。

### 4.4 LangChain RAG Chain（实验）

| 项目 | 值 |
|---|---|
| 位置 | `app/chains/langchain_rag_chain.py` |
| 框架 | LangChain ChatPromptTemplate + ChatOpenAI + LCEL |
| 用途 | LangGraph Agent 的 `rag_node` 内部调用 |
| 与主链路差异 | Prompt 模板不同，无 Query Rewrite 支持 |
| 状态 | 🧪 实验模块，不替换 `/agent/chat` |

### 4.5 traceId 实现

| 层 | 实现 | 位置 |
|---|---|---|
| Frontend | `crypto.randomUUID()` → `X-Trace-Id` header | `frontend/src/App.jsx` |
| Java | `TraceIdFilter` → MDC + request.setAttribute + 透传 header | `backend-java/.../filter/TraceIdFilter.java` |
| Python | `trace_id_middleware` → `request.state.trace_id` + 响应头 | `app/main.py:14` |
| 响应 | header `X-Trace-Id` + body `traceId` 字段 | 全链路 |

**兜底机制：** 任何一环缺失 traceId 都会自动生成 UUID 兜底。

---

## 5. Evaluation 盘点

### 5.1 评估脚本清单

| 脚本 | 位置 | 功能 | 是否调用 LLM |
|---|---|---|---|
| `eval_retrieval.py` | `scripts/eval/` | 检索评估（source_hit + keyword_hit） | ❌ 零 token |
| `eval_generation.py` | `scripts/eval/` | 生成评估（answer 关键词命中 + 拒答检查） | ✅ 调用 LLM |
| `compare_eval_reports.py` | `scripts/eval/` | Baseline vs Current 回归检查 | ❌ |
| `compare_topk_eval.py` | `scripts/eval/` | TopK 对比（3/5/8） | ✅ |
| `compare_query_rewrite.py` | `scripts/eval/` | Query Rewrite 对比（none vs rule） | ✅ |
| `update_eval_baseline.py` | `scripts/eval/` | 更新 baseline 文件 | ❌ |
| `run_rag_eval.py` | `scripts/eval/` | 一键运行 retrieval + generation + regression | ✅ |

### 5.2 Retrieval Evaluation

| 项目 | 值 |
|---|---|
| 评估内容 | `source_hit`（TopK 是否命中预期来源）+ `keyword_hit`（TopK 内容是否包含预期关键词） |
| answerable case | 检查 source_hit + keyword_hit |
| no-answer case | SKIP，不判 fail，只记录检索结果 |
| 输出报告 | `data/eval/reports/retrieval_eval_report.json` |
| 状态 | ✅ 完整闭环 |

### 5.3 Generation Evaluation

| 项目 | 值 |
|---|---|
| 评估内容 | answer 关键词命中 + 拒答检查 |
| answerable case | 检查 `expected_answer_keywords`（AND）+ `keyword_groups`（组内 OR，组间 AND），任一通过即通过 |
| no-answer case | 检查是否包含拒答关键词（`REFUSAL_KEYWORDS` 列表） |
| 文本归一化 | 全角→半角、中文数字→阿拉伯数字、空格去除、同义错字兼容 |
| retry 机制 | 第一次 FAIL 后自动 retry 一次，区分 flaky 和稳定失败 |
| 输出报告 | `data/eval/reports/generation_eval_report.json` |
| 状态 | ✅ 完整闭环 |

### 5.4 No-Answer Case

| 项目 | 值 |
|---|---|
| 数量 | 10 个（`none_001-007` + `colloquial_none_001-003`） |
| 评估方式 | 检查 answer 是否包含拒答关键词 |
| 拒答关键词列表 | `未找到, 没有找到, 暂无相关, 当前知识库, 没有明确依据, 建议联系, 无法确定, 信息不足, 未涉及, 未提及, 不在知识库, 没有相关信息, 不确定, 请联系, 咨询` |
| 当前状态 | 10/10 稳定拒答 |

### 5.5 keyword_groups

| 项目 | 值 |
|---|---|
| 位置 | `rag_eval_cases.json` 中 `expected_answer_keyword_groups` 字段 |
| 逻辑 | 组内 OR（命中任意一个即可）、组间 AND（每组都必须命中至少一个） |
| 当前使用 | `colloquial_006`（请假谁来批）— 审批角色同义词组 |
| 兼容性 | 与 `expected_answer_keywords` 共存，任一通过即通过 |
| 状态 | ✅ 可用 |

### 5.6 failure_type

| 值 | 含义 | 判断条件 |
|---|---|---|
| `passed` | 通过 | keywords_pass 或 groups_pass |
| `keyword_too_strict` | 关键词过严 | 有 keyword_groups 且 groups_pass=False |
| `generation_incomplete` | 模型没答全 | 无 keyword_groups 且 keywords_pass=False |
| `llm_flaky` | LLM 输出波动 | 首次 FAIL，retry 后 PASS |
| `no_answer_leakage` | 无答案场景泄漏 | no-answer case 拒答检查失败 |

### 5.7 Baseline / Regression Check

| 项目 | 值 |
|---|---|
| Baseline 路径 | `data/eval/baselines/generation_eval_baseline.json` + `retrieval_eval_baseline.json` |
| 对比脚本 | `scripts/eval/compare_eval_reports.py` |
| 判断逻辑 | case 级别 PASS→FAIL 为 regression；汇总指标下降为 regression |
| 退出码 | `0` = NO REGRESSION, `1` = REGRESSION DETECTED, `2` = 输入错误 |
| 更新方式 | 手动运行 `update_eval_baseline.py`（需全部 case 通过 + 人工审核） |
| 状态 | ✅ 可用 |

### 5.8 当前 38 Cases 状态

**总计：38 个 case**

| 类别 | 数量 | ID 范围 |
|---|---|---|
| Answerable（标准问法） | 18 | `leave_001-015`, `it_001-002`, `onboard_001` |
| Answerable（口语化问法） | 10 | `colloquial_001-010` |
| No-answer（标准问法） | 7 | `none_001-007` |
| No-answer（口语化问法） | 3 | `colloquial_none_001-003` |
| **合计** | **38** | answerable=28, no-answer=10 |

**当前评估结果（两种模式均 100%）：**

| 模式 | Retrieval | Generation | No-answer |
|---|---|---|---|
| none（默认） | 100% | 100% (28/28) | 100% (10/10) |
| rule（实验） | 100% | 100% (28/28) | 100% (10/10) |

**failure_type 分布：** 38/38 均为 `passed`

### 5.9 当前评估结果能说明什么，不能说明什么

**能说明：**
- 评估体系可以完整跑通（retrieval + generation + regression）
- 当前 38 个 case 下 RAG 链路工作正常
- No-answer 拒答能力在当前 10 个负样本下稳定
- Query Rewrite rule 模式在当前口语化 case 下有效
- flaky 检测机制可区分 LLM 随机波动和稳定失败
- baseline 回归检查机制可用

**不能说明：**
- ❌ RAG 系统完全可靠（仅 38 个 case，覆盖场景有限）
- ❌ 所有企业制度问题都能正确回答（知识库仅含 HR/IT/Banking 样例文档）
- ❌ Safety Guard 能拦截所有有害输入（仅关键词匹配，无语义理解）
- ❌ hybrid_rerank 模式有显著提升（当前评估集上提升不显著）
- ❌ 系统可直接用于生产（无认证、无审计、无监控、无限流）
- ❌ LLM 输出完全稳定（flaky 机制存在说明有波动可能）

---

## 6. API 契约一致性检查

### 6.1 检查范围

对比 `docs/api.md`、`docs/agent-collaboration/02-api-contract.md` 与实际代码（`app/main.py` + `app/schemas/chat_schema.py`）。

### 6.2 一致性检查结果

| 检查项 | 文档描述 | 代码实际 | 一致性 |
|---|---|---|---|
| `POST /agent/chat` 请求 | `{"message": "..."}` | `ChatRequest(message=str)` | ✅ 一致 |
| `POST /agent/chat` 响应 | `answer, model, traceId, success` | `ChatResponse(answer, model, traceId, success)` | ✅ 一致 |
| `POST /agent/langgraph/chat` 请求 | `{"message": "..."}` | `ChatRequest(message=str)` | ✅ 一致 |
| `POST /agent/langgraph/chat` 响应 | `answer, route, safe, category, reason, sources, success, traceId` | `AgentResponse(同上)` | ✅ 一致 |
| `GET /agent/health` 响应 | `{"service": "agent-python", "status": "UP"}` | `{"service": "agent-python", "status": "UP"}` | ✅ 一致 |
| traceId 透传 | header + body | middleware + response | ✅ 一致 |

### 6.3 发现的不一致

| # | 文件 | 不一致描述 | 严重程度 |
|---|---|---|---|
| 1 | `docs/api.md:19` | Health 响应写 `{"service": "agent-python", "status": "UP"}`，但 `docs/local-demo-guide.md:111` 写 `{"status": "ok", "agent_ready": true}` — 两处文档描述不一致，代码实际为 `{"service": "agent-python", "status": "UP"}` | 🟡 低 |
| 2 | `docs/agent-collaboration/02-api-contract.md:26` | `/api/chat` 响应包含 `sources` 字段，但 `docs/api.md:61` 明确说"当前版本 `/api/chat` 响应中不包含 `sources` 字段" | 🟡 低 |
| 3 | `docs/agent-collaboration/02-api-contract.md:53` | `/api/agent/langgraph/chat` 响应示例缺少 `route, safe, category, reason, sources` 字段 | 🟡 低 |
| 4 | `docs/agent-collaboration/02-api-contract.md:85` | 内部接口 `/agent/chat` 请求包含 `trace_id` 字段，但实际 `ChatRequest` schema 只有 `message` 字段，`trace_id` 通过 header 透传 | 🟡 低 |

### 6.4 DTO 契约风险

当前 Java ↔ Python 的 DTO 手动对齐，无共享 schema、无编译时校验。`ChatRequest` 只有 `message` 一个字段，结构简单，当前风险较低。但如果后续新增字段（如 `session_id`、`top_k`），需要同步更新 Java DTO + Python Schema + 文档。

---

## 7. 当前缺失能力

### 7.1 Python AI 服务侧

| 缺失能力 | 影响 | 优先级 |
|---|---|---|
| 多轮对话上下文管理 | 无法处理追问、指代消解 | 🟡 中 |
| LLM-based Query Rewrite | 规则版覆盖有限，无法处理未预见的口语表达 | 🟢 低 |
| LLM-based Tool Calling | 当前 Agent 路由为规则匹配，非 LLM 自主决策 | 🟢 低 |
| 向量数据库（Qdrant/Milvus） | 当前 FAISS 内存索引，不支持增量更新和大规模检索 | 🟢 低 |
| 文档上传 API | 知识库文档手动管理，无自动化入库 | 🟡 中 |
| 流式响应（SSE/WebSocket） | 无法实现打字机效果 | 🟢 低 |
| Python 单元测试 | 无自动化测试覆盖 | 🟡 中 |

### 7.2 Evaluation 侧

| 缺失能力 | 影响 | 优先级 |
|---|---|---|
| 评估集规模扩展 | 38 个 case 覆盖场景有限，无法代表真实用户分布 | 🔴 高 |
| 对抗样本 | 无恶意问题、边界条件、长文本等测试 | 🟡 中 |
| 多领域覆盖 | 仅 HR/IT/Banking 样例，缺财务、法务、行政等领域 | 🟡 中 |
| 评估结果可视化 | 仅有 JSON 报告，无趋势图表 | 🟢 低 |
| CI 集成评估门禁 | `compare_eval_reports.py` 可用但未接入 CI 流程 | 🟡 中 |

---

## 8. 风险点

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| 1 | 评估集规模小 | 🔴 高 | 38 个 case 无法代表真实场景，100% 通过率有误导性 |
| 2 | DTO 契约无强类型 | 🟡 中 | Java ↔ Python 手动对齐，新增字段可能不同步 |
| 3 | 无 Python 单元测试 | 🟡 中 | 检索、Prompt、Service 层无自动化测试 |
| 4 | Safety Guard 仅关键词匹配 | 🟡 中 | 无法处理变体表达、谐音、英文等绕过方式 |
| 5 | 两个 RAG Prompt 模板不一致 | 🟡 中 | `system_prompt.py` vs `langchain_rag_chain.py`，Agent 链路和主链路的 Prompt 规则不同 |
| 6 | HuggingFace 离线依赖 | 🟡 中 | 国内网络必须 `HF_HUB_OFFLINE=1`，模型需预下载 |
| 7 | 知识库规模小 | 🟡 中 | 仅 33 个文档片段，覆盖 HR/IT/Banking 样例 |
| 8 | 无认证/限流 | 🟡 中 | Python 服务无任何访问控制 |
| 9 | LLM 调用无重试/超时 | 🟢 低 | `llm_service.py` 无 retry、无 timeout 配置 |
| 10 | BM25 索引模块加载时构建 | 🟢 低 | 大知识库时启动慢，无持久化索引 |

---

## 9. 建议后续任务

### 9.1 短期（Phase 3 质量加固）

| 建议 | 关联 Task | 说明 |
|---|---|---|
| 补充 Python 单元测试 | TASK-024 | 覆盖 retrieval、prompt、service 核心逻辑 |
| 统一两个 RAG Prompt 模板 | — | `system_prompt.py` 和 `langchain_rag_chain.py` 应保持一致 |
| 修正文档不一致 | — | 修复 §6.3 中发现的 4 处文档不一致 |
| Safety Guard 补充测试 | — | 验证 5 类风险关键词的边界情况 |

### 9.2 中期（Phase 4 体验优化）

| 建议 | 关联 Task | 说明 |
|---|---|---|
| 扩充 Eval Cases | TASK-033 | 新增 ≥10 个 case，覆盖更多场景和边界条件 |
| 优化 Query Rewrite 规则 | TASK-034 | 覆盖更多口语化表达 |
| CI 集成评估门禁 | — | 将 `run_rag_eval.py --with-baseline` 接入 GitHub Actions |

### 9.3 长期（Roadmap）

| 建议 | 说明 |
|---|---|
| 多轮对话上下文管理 | 支持追问、指代消解 |
| 文档上传 API | 自动化知识库入库 |
| 向量数据库迁移 | FAISS → Qdrant/Milvus |
| LLM-based Query Rewrite | 替代规则版，覆盖更广 |
| 流式响应 | SSE 打字机效果 |

---

## 10. 是否建议进入开发阶段

**结论：建议有条件进入 Phase 3（质量加固），不建议直接进入 Phase 4（体验优化）。**

**理由：**

1. **Phase 2 盘点已完成**：Python AI 服务各模块状态清晰，风险点已识别。
2. **核心链路稳定**：RAG 主链路（`/agent/chat`）和 Agent 链路（`/agent/langgraph/chat`）均可正常工作。
3. **评估闭环完整**：retrieval + generation + regression 全链路可跑通。
4. **但存在前置条件**：
   - 应先完成 Python 单元测试（TASK-024），确保改动有安全网。
   - 应先修正文档不一致（§6.3），避免后续开发基于错误契约。
   - 应先确认 Prompt 模板一致性，避免 Agent 链路和主链路行为差异。

**不建议直接进入 Phase 4 的原因：**

- 缺少单元测试保护，新增功能可能引入回归。
- 评估集规模小，无法验证新功能的质量影响。
- 文档契约不一致，可能导致跨层开发出错。

---

## 附录：读取的关键文件清单

| # | 文件路径 | 用途 |
|---|---|---|
| 1 | `README.md` | 项目全貌 |
| 2 | `docs/local-demo-guide.md` | 本地演示指南 |
| 3 | `docs/demo-script.md` | 面试演示脚本 |
| 4 | `docs/rag-quality-engineering.md` | RAG 质量工程文档 |
| 5 | `docs/architecture.md` | 架构说明 |
| 6 | `docs/api.md` | 接口文档 |
| 7 | `docs/agent-collaboration/00-project-context.md` | 项目上下文 |
| 8 | `docs/agent-collaboration/01-architecture-boundary.md` | 架构边界 |
| 9 | `docs/agent-collaboration/02-api-contract.md` | API 契约 |
| 10 | `docs/agent-collaboration/03-agent-registry.md` | Agent 注册表 |
| 11 | `docs/agent-collaboration/04-task-board.md` | 任务看板 |
| 12 | `docs/agent-collaboration/06-do-not-touch.md` | 不可修改清单 |
| 13 | `docs/agent-collaboration/dashboard.md` | 协作仪表盘 |
| 14 | `agent-python/app/main.py` | FastAPI 入口 |
| 15 | `agent-python/app/core/config.py` | 配置 |
| 16 | `agent-python/app/schemas/chat_schema.py` | Pydantic Schema |
| 17 | `agent-python/app/services/rag_service.py` | RAG 主服务 |
| 18 | `agent-python/app/services/llm_service.py` | LLM 调用 |
| 19 | `agent-python/app/retrieval/hybrid_retriever.py` | 统一检索入口 |
| 20 | `agent-python/app/retrieval/faiss_retriever.py` | Faiss 向量检索 |
| 21 | `agent-python/app/retrieval/bm25_retriever.py` | BM25 检索 |
| 22 | `agent-python/app/retrieval/keyword_retriever.py` | 关键词检索 |
| 23 | `agent-python/app/retrieval/query_rewriter.py` | Query Rewrite |
| 24 | `agent-python/app/retrieval/cross_encoder_reranker.py` | Cross Encoder |
| 25 | `agent-python/app/prompts/system_prompt.py` | Prompt 模板 |
| 26 | `agent-python/app/chains/langchain_rag_chain.py` | LangChain RAG |
| 27 | `agent-python/app/tools/rag_tools.py` | Agent Tools |
| 28 | `agent-python/app/agents/langgraph_agent.py` | LangGraph Agent |
| 29 | `agent-python/app/guards/safety_guard.py` | Safety Guard |
| 30 | `agent-python/scripts/eval/run_rag_eval.py` | 一键评估 |
| 31 | `agent-python/scripts/eval/eval_retrieval.py` | 检索评估 |
| 32 | `agent-python/scripts/eval/eval_generation.py` | 生成评估 |
| 33 | `agent-python/scripts/eval/compare_eval_reports.py` | 回归检查 |
| 34 | `data/eval/rag_eval_cases.json` | 评估用例集 |
