# RAG 生成前检索相关性 Gate 实验报告

## 1. 问题背景

Enterprise AI Copilot 的 RAG 链路会先检索知识库，再把 TopK chunk 交给大语言模型生成回答。对于知识库没有直接答案的问题，向量检索和 BM25 仍可能召回主题相近的文档，模型随后依赖 Prompt 自行判断是否拒答。

本实验研究一个受限问题：能否在生成前仅使用现有 FAISS 与 BM25 原始分数，确定性地识别“知识库证据不足”的请求，从而避免不必要的模型调用。

## 2. 原链路为什么无答案仍调用 LLM

FAISS 和 BM25 都是 TopK 排序检索器，而不是答案充分性分类器。只要索引非空，它们通常都会返回候选；RRF 也只融合名次，不表达“知识库能否回答”。因此普通 RAG 和 LangGraph 在取得非空候选后仍会调用各自原有 LLM，由生成 Prompt 负责要求模型在证据不足时拒答。

本实验没有改变这一公开行为。Shadow block 仍继续原生成链路，当前没有减少实际 LLM 调用。

## 3. Scored Retrieval 设计

FAISS 使用归一化向量上的 `IndexFlatIP`，原始分数等价于 cosine similarity，越大越相似。实现新增内部 scored API，同时保留原 `retrieve()` 返回 chunk 列表的兼容行为。

BM25 继续使用现有 `retrieve_with_scores()`。Hybrid Retrieval 按 `chunk_id` 合并两类信号：

```text
CandidateSignals
├── chunk_id
├── vector_score / vector_rank
└── bm25_score / bm25_rank
```

缺失的召回信号保持 `None`，不使用其他 chunk 的分数填充。RRF 继续只负责最终排序，不作为绝对门控阈值。

## 4. 同候选 Vector/BM25 规则

首轮实验规则为：

```text
candidate_pass =
    vector_score >= vector_strong
    OR (
        vector_score >= vector_weak
        AND bm25_score >= bm25_weak
    )
```

整体 answerable 为任意一个候选通过。Vector 和 BM25 必须属于同一个 chunk；BM25 不能单独放行，也不能跨候选组合。

首轮参数为：

```text
vector_strong = 0.65
vector_weak   = 0.61
bm25_weak     = 2.10
```

这些数值仅用于复现实验，不是经过验证的可部署参数。

## 5. Shadow 与 fail-open 设计

Gate 支持：

- `off`：默认模式，不计算实际拦截决策，不改变原链路。
- `shadow`：显式启用，计算并记录决策，但不因 block 提前返回。
- `enforce`：禁止使用，配置后服务启动失败。

Shadow evaluator 自身异常时使用 fail-open：记录 traceId、异常类型和非敏感决策字段，然后继续原生成链路。该异常保护只覆盖 Gate evaluator，不会掩盖检索、Query Rewrite、Prompt 或 LLM 异常。

空候选保持 Gate 接入前的历史行为：普通 RAG 继续原 Prompt/LLM 流程；LangGraph RAG 返回原有固定无答案结果。

## 6. 初始 38 条结果

旧评估集包含 28 条 answerable 与 10 条 no-answer。严格改为同候选信号后，首轮阈值得到：

| 指标 | 结果 |
|---|---:|
| answerable pass | 25/28 |
| answerable false reject | 3 |
| no-answer shadow block | 10/10 |
| no-answer shadow pass | 0 |

三条误拒均为口语化短查询。该数据同时参与了最初阈值观察，不能作为独立验证效果或生产准确率。

## 7. 为什么重新构建独立验证集

旧 38 条数据规模小、主题简单，并参与过阈值选择。为降低数据泄漏，实验重新人工复核了独立候选集，将直接有知识库依据的问题标为 answerable，将只有主题重叠但缺少具体答案的问题标为 no-answer，并把跨文档冲突和部分可回答问题移入独立 `needs_review`。

最终主数据为 20 条 answerable、19 条 no-answer，另有 5 条 needs_review。数据在打分前通过稳定 ID 固定为 Calibration 和一次性 Holdout。

## 8. Calibration 划分与结果

Calibration 包含 12 条 answerable 和 11 条 no-answer。比较了：

- 7 个纯 Vector 阈值；
- 76 个合法的“Vector strong 或同候选双 weak”组合。

候选范围内没有零误拒规则。按“先最小化误拒，再最大化 no-answer block、边界与简洁度”的顺序，相对最优候选为纯 Vector `0.58`：

| 指标 | 结果 |
|---|---:|
| answerable pass | 10/12 |
| answerable false reject | 2 |
| no-answer block | 4/11 |
| no-answer false pass | 7 |

Calibration 中 answerable 最大 Vector 范围为 `0.504800～0.749412`，no-answer 为 `0.522416～0.694253`，两类明显重叠。

## 9. Holdout 一次性验证结果

Holdout 在规则冻结后只执行一次，包含 8 条 answerable 与 8 条 no-answer：

| 指标 | 结果 |
|---|---:|
| answerable pass | 7/8 |
| answerable false reject | 1 |
| no-answer block | 1/8 |
| no-answer false pass | 7 |

该结果同时未达到 answerable `8/8` 和 no-answer block `>=6/8` 的最低标准。实验没有在同一 Holdout 上继续调参，也没有修改生产阈值。

## 10. 边界样例

以冻结候选 `Vector >= 0.58` 为例：

- `val_n_010`（午休外出审批）：`0.583249`，仅高于阈值 `0.003249`，错误放行。
- `val_n_008`（线上病假截图）：`0.587926`，错误放行。
- `val_a_007`（虚拟专网登录）：`0.559544`，错误拦截。
- `val_n_014`（试用期带薪病假）：最高相关度约 `0.734644`，尽管证据不足，仍被高置信度放行。

这些样例说明微调阈值无法消除主题相似与证据充分之间的差异。

## 11. Partial-answer 观察

4 条 partial 样例在冻结候选规则下均被 Gate 判为 pass，符合“至少一个核心子问题有依据时优先放行”的原则。实际生成观察中：

- 3 条能够分别回答有依据部分并指出无依据部分；
- 1 条整体拒答，遗漏了可回答的子问题。

当前 Prompt 没有为 partial-answer 做专门拆分，本实验按约束未修改 Prompt。

## 12. 为什么相关性不等于答案充分性

Vector 与 BM25 衡量 query 和文档的语义或词法相关性。一个问题可能与 VPN、年假、病假等文档高度相关，但询问的具体字段——设备数量、审批耗时、金额上限、手机端步骤——并未出现在文档中。

因此，高相关分数只能说明“文档谈论相近主题”，不能证明“文档包含回答该问题所需的事实”。双路弱信号也无法解决这一点，因为高重叠 no-answer 往往同时具有高 Vector 和高 BM25。

## 13. 为什么未修改生产阈值

Calibration 的相对最优 `0.58` 在一次性 Holdout 上只拦截 `1/8` no-answer，并误拒 `1/8` answerable。把该阈值写入默认配置会把失败实验结果误当作部署参数。

因此首轮阈值仅保留用于复现实验，生产默认模式改为 `off`。

## 14. 为什么禁止 Enforcement

Enforcement 会让 Gate block 真正阻断生成。当前 Holdout 已证明该决策不可靠，启用后会同时造成正常问题误拒和无答案问题漏放。

配置层继续明确拒绝 `RAG_GATE_MODE=enforce`，而不是提供一个未验证的隐藏开关。

## 15. 当前最终状态

- Scored Retrieval、同候选信号合并、Shadow 日志和 evaluator fail-open 已实现。
- Gate 默认模式为 `off`；只有显式设置 `RAG_GATE_MODE=shadow` 才启用分析。
- 当前没有实现可靠的生成前无答案拦截。
- 当前没有减少实际 LLM 调用。
- Shadow 数据不能作为生产准确率。
- 实验未使用 LLM-as-Judge、Cross Encoder Gate 或第二个大语言模型裁判。
- 失败实验没有被包装成上线成果或生产能力。

## 16. 后续可研究方向

后续如重新立项，应使用新的 Calibration 与第二批一次性 Holdout，研究答案证据充分性而非继续调相关性阈值，例如：

- 基于结构化事实字段或规则的 evidence coverage；
- 面向具体领域的答案槽位覆盖率；
- 将 partial-answer 作为独立任务评估；
- 更丰富的高主题重叠 no-answer 数据；
- 在不影响稳定主链路的前提下继续 Shadow 采样。

在新方案通过独立验证前，Gate 应保持 `off`，不得进入 Enforcement。
