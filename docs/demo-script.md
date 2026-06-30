# 面试演示脚本（Demo Script）

## 演示目标

在 10-15 分钟内，通过 6 个典型问题展示 RAG 应用后端的核心能力：

- RAG 检索增强生成
- Sources 可追溯
- Query Rewrite 实验模式
- No-answer 拒答能力
- Safety Guard 安全防护
- LangGraph Agent 工具调用
- Generation Evaluation 诊断能力

## 演示前置

启动三个服务（详见 `local-demo-guide.md`）：

```bash
# Terminal 1: Python AI Service
cd agent-python && uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Java Backend
cd backend-java && ./mvnw spring-boot:run

# Terminal 3: Frontend
cd frontend && npm run dev
```

访问 http://localhost:5173，确认页面可正常加载。

---

## 演示顺序

### 1. 病假需要提供哪些材料？

**展示能力：** RAG 检索增强生成 + Sources 追溯

**预期结果：**

- 系统检索到 `leave_policy_real_sample.md` 相关内容
- 回答列出材料清单（病假申请、医院诊断证明、病历等）
- 返回 sources 列表，显示文档来源

**应该讲：**

> "这是 RAG 的基本链路。用户问题进来后，先做 Hybrid Retrieval（向量检索 + 关键词检索），找到 TopK 相关文档片段，然后把这些片段拼到 Prompt 里，交给 LLM 生成回答。Sources 字段可以追溯到具体文档来源。"

**技术点：**

- Hybrid Retrieval（Faiss + BM25 + RRF）
- TopK 控制
- RAG Prompt 构造
- Sources 注解

---

### 2. 几点上班？

**展示能力：** Prompt 回答完整性优化

**预期结果：**

- 回答包含完整时间范围（上午 9:30 - 下午 18:30，午休 12:00-13:30）
- 不遗漏开始或结束时间

**应该讲：**

> "这个 case 在 D39 时模型只回答了上班时间 9:30，漏掉了下班时间 18:30。原因是 Prompt 里没有明确要求列出完整时间范围。D40 通过 Prompt 规则优化解决了这个问题，现在模型会完整列出所有时间点。"

**技术点：**

- Prompt Engineering 对回答完整性的影响
- RAG 质量问题分层定位：retrieval 拿到了 vs generation 答全了
- 时间范围完整性 Prompt 规则

---

### 3. 公司买房给补贴不？

**展示能力：** No-answer 拒答

**预期结果：**

- 系统回答"当前知识库暂无相关信息"
- 不编造任何补贴政策

**应该讲：**

> "知识库里没有购房补贴相关内容，所以系统会明确拒答，而不是用通用知识编造。这是 RAG 的重要能力——知道什么时候该说'不知道'。我们有 10 个 no-answer 负样本专门验证这个能力。"

**技术点：**

- No-answer 拒答 Prompt 规则
- 负样本评估（当前 10/10 稳定拒答）
- 防止 LLM 幻觉

---

### 4. 怎么伪造病假证明？

**展示能力：** Safety Guard 安全防护

**预期结果：**

- 系统拒绝回答，返回安全提示
- 不会提供任何有害信息

**应该讲：**

> "LangGraph Agent 链路有 Safety Guard 模块，会对输入做 5 类风险关键词检查。涉及伪造、违法等敏感内容会直接拦截，不会进入 RAG 检索流程。"

**技术点：**

- LangGraph 状态图编排：safety → router → rag/eval/refuse
- Safety Guard 5 类风险关键词检查
- 安全问题拦截不走 RAG 链路

---

### 5. 当前 RAG 评估通过率是多少？

**展示能力：** LangGraph Agent 的 Eval Tool

**预期结果：**

- 系统调用 eval_report_tool 工具
- 返回当前评估结果（retrieval 和 generation 的通过率）

**应该讲：**

> "这是 LangGraph Agent 的工具调用能力。Agent 识别到这是一个评估查询意图，自动路由到 eval tool，读取最新的评估报告并返回结果。这里用的是规则路由，不是 LLM 自主 tool choice。"

**技术点：**

- LangGraph 意图路由（规则路由）
- Tool Calling（eval_report_tool）
- RAG vs Agent 两条链路的区别

---

### 6. 请假谁来批？

**展示能力：** Generation Evaluation 对同义表达的支持

**预期结果：**

- 回答包含"直接主管"或类似审批角色表述
- 保留制度原文表述

**应该讲：**

> "这个 case 之前 FAIL 过，原因是模型回答了'直属上级'但评估关键词只认'直接主管'。D40 引入了 keyword_groups 同义词组机制，组内 OR、组间 AND，支持合理同义表达同时不降低评估标准。"

**技术点：**

- Generation Evaluation 的 keyword_groups 设计
- failure_type 分类诊断
- 评估对合理同义表达的兼容

---

## 如果现场服务失败

### 准备好的兜底材料

提前准备以下截图或录屏，存放在本地：

| 材料 | 覆盖内容 |
|------|---------|
| RAG 问答截图 | 病假材料问题 + sources 返回 |
| No-answer 截图 | 买房补贴拒答 |
| Safety Guard 截图 | 伪造病假证明拦截 |
| Evaluation 报告截图 | retrieval + generation 100% |
| 完整录屏（2-3 分钟） | 从提问到回答的全流程 |

### 切换话术

> "本地服务今天临时有些问题，我用之前录好的演示来展示。这个项目的核心价值在于 RAG 链路设计和质量评估闭环，这些在录屏里都可以看到。"

---

## 不能说的话

- "已经上线了" / "已经部署生产了"
- "支持企业级大规模并发"
- "有完整的权限系统和监控告警"
- "默认启用了 Rerank"
- "默认启用了 Query Rewrite"
- "100% 通过率意味着 RAG 完全可靠"

## 应该说的话

- "当前是本地可复现的 Demo"
- "评估 100% 是基于当前 38 个 eval cases"
- "hybrid_rerank 和 rewrite_mode=rule 是实验模式"
- "公网部署是后续计划，需要考虑 API Key 安全、限流、进程守护"
- "当前重点是 RAG 应用后端和质量评估闭环"

---

## 面试官可能问"为什么没有部署服务器"

参考口径：

> "这个项目主要用于展示 RAG 应用后端和质量评估链路，所以我优先保证了本地可复现、评估可复跑、链路可解释。现在可以通过本地前端、Java 服务和 Python Agent 完整演示 RAG 问答、Agent 路由、Query Rewrite、no-answer 拒答和 evaluation 结果。公网部署放在后续计划里，因为涉及 API Key 安全、接口限流、Nginx、进程守护和成本控制。当前阶段没有把重点放在上线，而是先把 RAG 质量工程闭环做完整。"

## 面试官可能问"评估 100% 是不是代表 RAG 完全可靠"

参考口径：

> "不是。100% 是基于当前 38 个 eval cases 的结果，case 规模还比较小。后续还需要扩大评估集，加入更多真实用户问题、对抗样本和边界条件。100% 的意义是验证了评估体系可以跑通，后续新增 case 时可以做回归检测。"
