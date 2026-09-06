# RAG 质量工程

## 概述

Enterprise AI Copilot 的 RAG 质量优化不是一次性完成的，而是通过 D36-D40 五个迭代逐步构建的工程闭环。

本文档串起这个过程，说明每次迭代解决了什么问题、用了什么方法、当前达到什么状态、还有哪些局限。

## 质量优化链路

### D36：BM25 + RRF 混合检索

**问题：** 纯向量检索（Faiss）对关键词精确匹配不足。例如用户问"公司用什么 VPN"，向量检索可能返回 IT 相关内容但不一定命中 VPN 关键词。

**方案：**

- 新增 BM25 检索（字符级 n-gram，无外部依赖）
- 引入 RRF（Reciprocal Rank Fusion）融合多路排序
- 保留原有向量检索 + 关键词检索作为 `vector` 模式

**结果：** 新增 `hybrid` 模式（Faiss + BM25 + RRF），成为默认检索模式。

**技术决策：** RRF 不需要分数归一化，适合融合异构检索结果。

---

### D37：Cross Encoder 重排序实验模式

**问题：** RRF 融合后 TopK 内的排序质量仍有提升空间。某些 case 中，正确文档排在第 4-5 位，被 TopK=3 截断。

**方案：**

- 引入 Cross Encoder 精排（BAAI/bge-reranker-base）
- 先用 RRF 取 Top10 候选，再用 Cross Encoder 精排到 TopK
- 作为 `hybrid_rerank` 实验模式，不替换默认模式

**结果：** 实验模式可用，但当前评估集上提升不显著。

**技术决策：** Cross Encoder 比 Bi-Encoder 更精确但更慢，适合小候选集精排。

**当前状态：** `hybrid_rerank` 是实验模式，不建议默认启用。

---

### D38：Query Rewrite 实验模式

**问题：** 用户口语化问题与知识库书面表达不一致。例如"入职要交啥" vs "入职需要提交的材料"。

**方案：**

- 引入规则版 Query Rewrite（正则匹配，不调用 LLM）
- 只改写检索用 query，最终 prompt 中的用户问题不变
- 无匹配规则时返回原问题，不影响检索

**结果：** 仅作为历史离线 `rewrite_mode=rule` 实验模式保留。

**技术决策：** 规则版不消耗 token、延迟低、可预测，适合企业制度这类表达相对固定的场景。

**当前状态：** `rewrite_mode=rule` 已归类为 Legacy Experimental Rewrite，不进入生产链路或 CI 门禁。

---

### D39：口语化 Query Rewrite 评估集

**问题：** D38 的 Query Rewrite 只有规则实现，没有足够的口语化 case 验证效果。

**方案：**

- 新增 13 个口语化 eval cases（colloquial_001-013）
- 对比 `rewrite_mode=none` 和 `rewrite_mode=rule` 两种模式

**结果：**

以下是 Legacy Sample 阶段的历史对比快照，不代表当前 Corpus V2 的生产门禁结果。

| 模式 | Retrieval | Generation |
|------|-----------|------------|
| none | 96.4% (27/28) | 92.1% (35/38) |
| rule | 100% (28/28) | 92.1% (35/38) |

**发现：** retrieval 通过 rule 改善了，但 generation 没有同步提升。说明问题不是"搜没搜到"，而是"搜到了但没答全"。

---

### D40：生成评估诊断优化

**问题：** D39 暴露 3 个 generation FAIL，但无法区分失败原因。

**方案：**

1. **Prompt 回答完整性优化**
   - 时间范围完整性规则
   - 制度原词保留规则

2. **keyword_groups 同义词组**
   - 组内 OR、组间 AND
   - 支持合理同义表达，不降低评估标准

3. **failure_type 分类**
   - `passed`：通过
   - `keyword_too_strict`：关键词过严
   - `generation_incomplete`：模型没答全
   - `llm_flaky`：LLM 输出波动
   - `no_answer_leakage`：无答案场景泄漏

**结果：**

| 模式 | answerable | no-answer | overall |
|------|-----------|-----------|---------|
| none | 100% (28/28) | 100% (10/10) | 100% (38/38) |
| rule | 100% (28/28) | 100% (10/10) | 100% (38/38) |

**技术决策：** 100% 不代表 RAG 完全可靠，只代表当前 38 个 eval cases 下的闭环可跑通。后续需要扩大评估集。

上面的 Generation 表格与 `failure_type` 是 D40 的历史/离线快照，不代表当前 CI 使用 `rule` 作为生产路径。模型或接口出现 HTTP 200 但空响应时，当前诊断分类为 `LLM_API_INSTABILITY`，不归因于 Corpus 或 Retrieval Regression。

## Corpus V2 与 Eval V2 收口

当前正式 Corpus 是覆盖 HR、Finance、IT 的小型多文档 Synthetic Demo Corpus，用于测试真实的多文档竞争和跨文档引用关系，不代表真实企业知识库或真实 OA 数据。当前正式统计为 22 篇文档、217 chunks；Chunking、Embedding、BM25、RRF、candidate_k 和 final top_k 均未因本轮扩容调整。

HR 文档已经按职责收口：

| 文档 | 权威职责 |
| --- | --- |
| `hl_hr_annual_leave_policy.md` | 年假资格、额度、折算、结转、余额 |
| `hl_hr_leave_request_guide.md` | 请假申请、材料、审批、补件、撤回 |
| `hl_hr_attendance_policy.md` | 工作时间、打卡、迟到早退、考勤落账 |
| `hl_hr_sick_personal_leave_policy.md` | 病假、事假、证明及异常处理 |
| `hl_hr_overtime_comp_time_policy.md` | 加班、调休、晚间返程交通 |
| `hl_hr_benefits_holidays_guide.md` | 福利假概览及边界 |

旧 `leave_policy_real_sample.md` 已退出 Loader 扫描范围并归档，不再是当前 HR 权威知识源。Eval Baseline Migration 对旧 Oracle 做了重新审计：15 条 KEEP、16 条 REMAP、7 条 RETIRE_AND_REPLACE。其工程含义是 Corpus 权威关系变化后同步校准测试 Oracle，而不是为了把红 Case 改绿。

## Production Query Normalization

生产链路与 CI production Eval 共用唯一的 `normalize_retrieval_query()`：

```text
Original user query
  → normalize_retrieval_query()
  → BM25 + Vector + RRF
  → Top-K context
```

它只在已经进入 RAG 后作用于 retrieval query，不是新的 Intent Router、Planner 规则或业务动作路由。原始用户问题仍用于最终 Prompt；规范化不得增加用户没有表达的新意图。例如：

```text
年假咋请？ → 年假如何申请？
```

当前生产规则仅覆盖 `咋请`、`怎么请`、`咋申请` 这类明确的短口语申请表达，并要求短语边界成立；未命中时原样保留，不扩展为年假天数、余额或审批等额外意图。

完整的 `colloquial_003` 诊断记录如下，其他文档不重复硬编码这些排名：扩容后原 Query 的 BM25 rank 为 9，Vector Top-10 未命中正确 Chunk，RRF rank 为 13，Final Top-3 FAIL；窄规范化后 BM25 rank 为 6、Vector rank 为 4、RRF rank 为 2，Final Top-3 PASS。固定 Retrieval Eval 从 27/28 提升到 28/28，10/10 no-answer 保持不变。

`rewrite-mode=rule` 仍可用于离线 Query Rewrite 对照，但无生产调用方，不参与 Agent/Tool 主链路，也不作为 CI blocking gate。CI 使用 `--rewrite-mode production`，直接复用生产规范化实现，阈值没有因为 legacy 规则失败而放宽。

## Chunk Representation 实验

曾对检索表示做过临时实验：将 `Document Title + Section Title + Chunk Text` 拼接后建立实验索引。该方案没有解决 `年假咋请？` 的失败，反而强化了相似年假文档之间的竞争，因此没有进入生产，也没有覆盖正式索引或源 Markdown。

---

## 当前检索配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `retrieval_mode` | `hybrid` | Faiss + BM25 + RRF |
| 生产检索规范化 | `normalize_retrieval_query` | 生产入口固定执行的窄范围语义等价规范化 |
| Eval `--rewrite-mode` | `production` | 直接复用生产规范化实现；`none` 仅作离线对照 |
| `top_k` | 3 | 进入 Prompt 的文档片段数 |
| `rerank_model` | `BAAI/bge-reranker-base` | 仅 `hybrid_rerank` 模式使用 |

**实验模式（不默认启用）：**

| 模式 | 说明 |
|------|------|
| `hybrid_rerank` | Faiss + BM25 → RRF → Top10 候选 → Cross Encoder 精排 → TopK |
| `rewrite_mode=rule` | Legacy Experimental Rewrite；规则匹配重写，不调用 LLM，不进入生产或 CI |

## 当前评估结果

**检索评估**（不调用 LLM，零 token 消耗）：

当前生产门禁使用生产窄规范化模式：28/28 answerable、10/10 no-answer。
下表中的 `none` 与 `rule` 仅为历史/离线对照结果；`rule` 不再是当前门禁。

| 模式 | source_hit | keyword_hit | final_pass_rate |
|------|-----------|-------------|-----------------|
| production normalization | 100% | 100% | 100% |
| none | 100% | 96.4% | 96.4% |
| rule | 100% | 100% | 100% |

**生成评估历史快照**（调用 LLM）：

| 模式 | answerable | no-answer | overall |
|------|-----------|-----------|---------|
| none | 100% (28/28) | 100% (10/10) | 100% (38/38) |
| rule | 100% (28/28) | 100% (10/10) | 100% (38/38) |

**failure_type 分布：** 38/38 均为 `passed`

**No-answer 负样本：** 10/10 稳定拒答

## 评估体系能力

| 能力 | 说明 |
|------|------|
| 两层评估 | Retrieval（是否搜到）+ Generation（是否答对答全） |
| Flaky 检测 | retry 机制区分 LLM 随机波动和稳定失败 |
| Baseline 回归 | 手动更新 baseline，新增 case 时可回归检测 |
| TopK 对比 | 对比不同 TopK 下的 retrieval 和 generation 质量 |
| Query Rewrite 对比 | 对比 production normalization 与 Legacy `rule` 离线模式 |
| 中文数字归一化 | "三天" ↔ "3天" 兼容 |
| failure_type 分类 | 区分 5 种失败原因，辅助定位问题 |
| keyword_groups | 组内 OR、组间 AND，支持同义表达 |

## 当前局限

1. **评估集规模有限**：当前 38 个 eval cases，覆盖场景不够广泛
2. **知识库规模较小**：当前知识库仅包含 HR / IT / Banking 样例文档
3. **公网验证范围有限**：当前是小规格单机演示，不承诺生产 SLA
4. **无正式用户目录/RBAC**：Evaluation 已由 Java 验证 JWT 的 `role=ADMIN` 控制；`ADMIN_TOKEN` 仅保留为业务动作的 server-side hardening
5. **无多轮对话**：当前仅支持单轮问答
6. **无文档上传**：知识库文档手动管理
7. **hybrid_rerank 提升不显著**：当前评估集上 Cross Encoder 精排未带来明显收益
8. **生产规范化有意保持窄范围**：Legacy `rule` 仅覆盖部分实验性口语化表达，生产规范化不补充用户未表达的事实或意图

## 后续计划

- 扩大评估集（更多真实用户问题、对抗样本、边界条件）
- 补齐正式认证、监控告警和高可用部署
- 扩展 P0 scoped task memory 的场景覆盖与质量评估（不包含 Profile/Vector Memory）
- 文档上传与知识库管理接口
- 扩展长时间、多客户端容量验证
