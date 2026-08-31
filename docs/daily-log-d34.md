# D34 Daily Log — 扩展 RAG 评估集 + 无答案负样本

## 今日目标

增强 RAG evaluation：扩展评估集到 25 个 case，新增无答案负样本，支持 answerable / no-answer 分类评估。

## 修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `data/eval/rag_eval_cases.json` | 修改 | 8 → 25 case，新增 `answerable` 字段 |
| `agent-python/scripts/eval/eval_retrieval.py` | 修改 | 支持 answerable/no-answer 分类统计 |
| `agent-python/scripts/eval/eval_generation.py` | 修改 | 新增拒答检测逻辑 + 中文数字归一化 |
| `agent-python/scripts/eval/compare_eval_reports.py` | 修改 | 兼容新报告格式的 answerable/no-answer 字段 |

## 实现内容

### 评估集扩展

从 8 个 case 扩展到 25 个：

**HR 请假制度类（15 个，原有 8 + 新增 7）：**
- leave_001~008：原有（上班时间、请假提前期、病假材料、年假、旷工、迟到早退、婚假、产假）
- leave_009：丧假天数
- leave_010：病假工资计算
- leave_011：事假影响调薪的天数阈值
- leave_012：加班打车报销时间
- leave_013：婚假所需材料
- leave_014：请假审批流程
- leave_015：哺乳假时长

**IT 支持类（2 个）：**
- it_001：VPN 申请流程
- it_002：VPN 客户端使用

**入职类（1 个）：**
- onboard_001：新员工入职流程

**无答案负样本（7 个）：**
- none_001：公司股票期权怎么分配？
- none_002：员工购房补贴标准是什么？
- none_003：年终奖一定发几个月？
- none_004：公司是否提供商业保险？
- none_005：内推奖金是多少？
- none_006：离职补偿如何计算？
- none_007：可以远程办公几天？

### 新增字段

每个 case 新增 `answerable: true/false` 字段：
- `true`：有答案 case，检查 source_hit + keyword_hit
- `false`：无答案 case，检查拒答关键词命中

### eval_retrieval.py 改动

- answerable case：保持原有 source_hit + keyword_hit 逻辑
- no-answer case：标记为 SKIP，不判 fail，只记录检索结果
- 汇总统计区分 answerable_cases / no_answer_cases

### eval_generation.py 改动

- answerable case：保持原有关键词匹配逻辑
- no-answer case：新增 `_check_refusal()` 函数，检查回答是否包含拒答关键词（"未找到"、"当前知识库"、"建议联系"等 13 个）
- 新增 `normalize_text()` 中文小写数字 → 阿拉伯数字映射（一→1, 二→2, ..., 十→10），减少 LLM 措辞差异导致的误判
- 汇总统计区分 answerable_pass_rate / no_answer_pass_rate / overall_pass_rate

### Prompt 检查

当前 prompt 已满足要求，无需修改：
- build_rag_prompt 第 4 条规则："如果知识库中没有明确答案，请明确说明'当前知识库暂无相关信息'，不要猜测或编造。"
- chunks 为空时的兜底 prompt 也明确要求拒答。

## 知识库覆盖检查

### 财务/报销类

当前知识库 `data/` 目录下**不存在**财务/报销类独立文档。HR 请假制度中有"工伤报销"和"假期工资"相关内容，但不是通用报销制度（报销材料、发票要求、差旅报销、报销审批、报销时限等）。因此本轮**没有加入财务/报销有答案 case**。

### IT 支持类

当前知识库 IT 域仅有两个文件：
- `it/README.md`：标题文档
- `it/vpn_guide.md`：VPN 使用说明（3 行）

无密码重置、设备报修、软件安装、权限申请等内容，因此 IT answerable case 保持 2 个。

### 最终评估集构成

| 类别 | 数量 | 说明 |
|------|------|------|
| HR 请假制度 | 15 | leave_001~015 |
| IT 支持 | 2 | it_001~002 |
| 入职 | 1 | onboard_001 |
| 无答案负样本 | 7 | none_001~007 |
| **总计** | **25** | |

## 验证结果

### 检索评估
```
总用例数:              25
answerable 用例数:     18
no-answer 用例数:      7
answerable 通过:       18
answerable 失败:       0
source_hit_rate:       100.0%
keyword_hit_rate:      100.0%
final_pass_rate:       100.0%
```

### 生成评估
```
总用例数:                25
answerable 用例数:       18
answerable 通过:         18
answerable_pass_rate:    100.0%
no-answer 用例数:        7
no-answer 通过(拒答):    7
no_answer_pass_rate:     100.0%
overall_pass_rate:       100.0%
stable_pass_rate(首次):  100.0%
flaky 数量:              0
LLM 调用失败:            0
```

### 回归检查
- 检索：**NO REGRESSION** ✅
- 生成：**NO REGRESSION** ✅

### 最终评估集构成

| 类别 | 数量 |
|------|------|
| HR 请假制度 | 15 |
| IT 支持 | 2 |
| 入职 | 1 |
| 无答案负样本 | 7 |
| **总计** | **25** |

## 基线

**本次不自动更新 baseline。** 可以考虑人工确认后手动更新：
```bash
python agent-python/scripts/eval/update_eval_baseline.py
```

## 面试可讲点

1. **无答案负样本**：不只测"答对了什么"，还测"不该答的有没有乱答"。7 个知识库外的问题全部正确拒答，说明 RAG 管道不会编造不存在的制度。
2. **answerable/no-answer 分类评估**：两类 case 用不同标准评判——有答案的检查关键词命中，无答案的检查拒答关键词。分开统计 pass_rate，不会互相干扰。
3. **中文数字归一化**：LLM 可能写"三天"或"3天"，normalize_text 统一转为阿拉伯数字后比较，减少因措辞差异导致的误判。
4. **评估集从 8 到 25**：覆盖 HR（15）、IT（2）、入职（1）、无答案（7）四个维度，比单一域的 8 个 case 更能代表真实场景。

## 建议 commit message

```
feat: expand RAG evaluation set to 25 cases with unanswerable negative samples
```
