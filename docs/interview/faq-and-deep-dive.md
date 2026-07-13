# 面试追问 Q&A

> 面试官常见追问及回答。每个回答保持诚实，不吹过头。

---

## Q1：这个项目和普通知识库问答有什么区别？

**回答：**

普通知识库问答通常是单链路 RAG — 用户提问 → 检索 → 生成回答。

我的项目有几个不同：

1. **双链路设计** — 稳定 RAG 主链路和 LangGraph Agent 实验链路并行，主链路不依赖 LangChain，Agent 链路支持 Safety Guard + 意图路由 + Tool Calling
2. **Evaluation 闭环** — 不只是 RAG 能用，还能评估 RAG 效果。38 个 case，两层评估，支持 flaky 检测和 baseline 回归
3. **生产化加固** — 按安全风险做了 12 项修复，包括 Safety Guard、权限边界、traceId、超时、异常收敛
4. **工程化视角** — 不是调通 API 就完事，而是关注架构边界、契约对齐、部署准备

---

## Q2：为什么采用 Java + Python 双服务？

**回答：**

两个原因：

1. **技术生态** — Python 的 AI 生态（LangChain、LangGraph、FAISS、sentence-transformers）比 Java 成熟得多。RAG 检索、Embedding、Agent 编排用 Python 实现效率更高。
2. **职责分离** — Java 擅长控制面（权限、超时、异常兜底、CORS），Python 擅长数据面（检索、生成、评估）。分离后 AI 逻辑迭代不影响业务网关，如果未来换 LLM Provider 只改 Python。

代价是多了一层网络通信和契约维护，但对这个规模的项目是值得的。

---

## Q3：RAG 是怎么做的？

**回答：**

手写全链路，不依赖 LangChain。

1. **检索** — Hybrid Retrieval，三种模式可切换：
   - `vector`：Faiss 语义检索 + keyword 检索合并去重
   - `hybrid`（默认）：Faiss + BM25 + RRF 融合排序
   - `hybrid_rerank`（实验）：Hybrid 候选 + Cross Encoder 精排
2. **BM25** — 自研字符级 n-gram 分词，不依赖 jieba，对中文友好
3. **RRF** — Reciprocal Rank Fusion，不需要分数归一化，直接按排名融合
4. **Prompt** — TopK=3 的 chunk 拄给 LLM，Prompt 包含拒答规则（"当前知识库暂无相关信息，不要编造"）
5. **Query Rewrite** — 实验模式，规则匹配重写口语化问题

---

## Q4：LangGraph Agent 在项目里解决什么问题？

**回答：**

LangGraph Agent 是实验链路，解决 RAG 主链路无法处理的场景：

1. **意图路由** — 用户问"评估通过率"这类问题，RAG 主链路会去知识库检索，但知识库里没有评估数据。Agent 链路通过关键词匹配把这类问题路由到 eval_node，读取本地评估报告。
2. **安全拒答** — Safety Guard 检测到高风险问题，直接路由到 refuse_node，不进入检索。
3. **Tool Calling** — rag_node 和 eval_node 都是 LangChain Tool，方便后续扩展更多工具。

Agent 链路和 RAG 主链路并行运行，不替换稳定接口。

---

## Q5：Evaluation 为什么要做？

**回答：**

RAG 效果不能只靠"看起来对"。我需要量化回答：

1. **检索是否命中** — Retrieval Evaluation 检查 TopK 结果是否包含预期来源和关键词，零 token 消耗
2. **生成是否正确** — Generation Evaluation 调用 LLM 检查回答是否包含预期关键词，支持同义词组（keyword_groups）
3. **是否稳定** — flaky 检测：第一次 FAIL 后 retry 一次，区分随机波动和稳定失败
4. **是否退化** — baseline 回归：对比 baseline 和 current 报告，判断修改后是否退化

38 个 case 覆盖了有答案和无答案场景。

---

## Q6：你怎么证明 RAG 效果稳定？

**回答：**

1. **baseline 回归** — 每次修改后运行 `run_rag_eval.py --with-baseline`，如果有退化会报 REGRESSION DETECTED
2. **flaky 检测** — Generation Evaluation 第一次 FAIL 后自动 retry，区分 llm_flaky（随机波动）和 stable_fail（稳定失败）
3. **无答案负样本** — 10 个无答案 case，检查模型是否正确拒答而不是编造
4. **TopK 对比** — `compare_topk_eval.py` 对比不同 TopK 值的效果

局限：38 个 case 规模有限，不能覆盖所有场景。

---

## Q7：Safety Guard 覆盖了什么？没覆盖什么？

**回答：**

**覆盖了：**
- 5 类风险关键词匹配（50 个关键词）
- 违法违规 / 伪造材料（9 个关键词）
- 绕过企业制度 / 规避审批（13 个关键词）
- 网络安全攻击 / 黑客行为（12 个关键词）
- 删除审计 / 隐藏痕迹（7 个关键词）
- 越权访问 / 数据窃取（9 个关键词）
- 空查询拦截
- 覆盖 RAG + Agent 两条链路

**没覆盖：**
- 变体绕过（同音字、拼音、英文）
- Prompt Injection（"忽略系统提示词"类攻击）
- 间接注入（通过知识库文档注入的恶意内容）

当前是规则版基础防护，不是完整安全系统。

---

## Q8：为什么 Evaluation 要做权限限制？

**回答：**

Evaluation 报告包含内部质量数据 — 通过率、失败案例、flaky 数量、baseline 状态。这些是开发/运维信息，不应该暴露给普通用户。

暴露风险：
1. 泄露内部质量数据
2. 暴露评估体系细节（case 数量、baseline 状态）
3. 可被竞争对手分析产品质量

所以把 Evaluation 定位为管理员诊断能力，通过 Admin Token 保护。

---

## Q9：Admin Token 是不是完整认证系统？

**回答：**

不是。Admin Token 是最小权限方案，解决"普通用户不应访问 Evaluation"这个问题。

**优点：**
- 实现简单，不引入用户体系
- 配置即生效
- 权限判断集中在 Java 后端

**缺点：**
- 共享 Token，无 per-user 身份
- Token 泄露则全员管理员
- 无法审计"谁"访问了 eval

如果要上线，需要替换为 JWT + 用户体系。

---

## Q10：为什么不接 Spring Security / JWT？

**回答：**

当前项目定位是本地 Demo / 面试演示，不是生产系统。引入 Spring Security 或 JWT 会增加复杂度，但不增加 Demo 价值。

Admin Token 是最小方案：
- 一个配置项 + 一个请求头 + 一个 Java 判断
- 不引入用户表、Session 管理、Token 刷新
- 本地 Demo 零配置（token 为空时跳过检查）

如果要上线，第一步是替换为 JWT + 用户体系，第二步是接入 Spring Security。

---

## Q11：traceId 怎么设计？

**回答：**

1. **Java 入口统一生成** — `TraceIdFilter` 继承 `OncePerRequestFilter`，每个请求生成 UUID 格式 traceId
2. **不信任客户端** — 客户端传入的 `X-Trace-Id` 会验证格式，非法格式（含控制字符、超长、非 UUID）丢弃重新生成
3. **MDC 关联日志** — traceId 存入 SLF4J MDC，日志自动带上 `[traceId]`
4. **透传给 Python** — Java → Python 通过 `X-Trace-Id` header 透传
5. **响应返回** — 响应头和响应体都包含 traceId，前端展示

用户反馈问题时只需要提供 traceId，服务端通过日志就能定位全链路。

---

## Q12：timeout 和 fallback 怎么设计？

**回答：**

超时分三层：

| 层 | 超时 | 配置 |
|---|------|------|
| Java → Python 连接 | 3s | `python.agent.connect-timeout` |
| Java → Python 读取 | 40s | `python.agent.read-timeout` |
| Python → LLM | 30s | `LLM_TIMEOUT` |

任何一层超时都会返回兜底响应：
- Java 层：`success=false`，answer 为"当前 AI 服务暂时不可用"
- Python 层：`success=false`，answer 为"当前 AI 服务暂时不可用"

异常信息不暴露给用户，只记日志。用户通过 traceId 反馈问题，服务端通过日志排查。

并发过载不等待上述完整超时：Java/Python 各有 3 个并发槽，最多排队 500ms，未获得槽位就返回 HTTP 429 和 `Retry-After: 1`。

---

## Q13：CORS 为什么要收敛？

**回答：**

之前 `WebConfig.java` 用 `allowedOriginPatterns("*")` 允许任意来源访问，配合 `allowCredentials(true)` 可能被 CSRF 攻击。

修改为可配置白名单 `cors.allowed-origins`，默认只允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`。生产环境需要配置为实际域名。

---

## Q14：为什么 Python 服务不能暴露公网？

**回答：**

如果 Python FastAPI 的 8000 端口直接暴露公网，请求就可能绕过 Java。

风险：
1. 绕过 Java 层的安全检查（Safety Guard、输入校验、Admin Token）
2. 绕过 Java 层的超时控制
3. 攻击者可伪造 `X-Allow-Eval: true` 直接访问 Evaluation
4. 直接调用 LLM，绕过所有保护

当前部署已经按该边界处理：Python 只在 Docker 内网 `expose 8000`，无宿主机端口映射；Java 只绑定 `127.0.0.1:8080`，公网流量必须经过 Nginx。

---

## Q15：已经有公网 Demo，为什么仍不是生产系统？

**回答：**

1. 只有共享 Admin Token，没有 JWT / RBAC 和 per-user 身份
2. 单机 Compose，无高可用和自动扩缩容
3. 没有生产级 APM、集中日志和告警
4. Safety Guard 仍是规则版，评估集只有 38 个 case
5. 有界并发机制需要在目标服务器完成容量压测后才能给出 QPS/P95 结论

公网地址是功能演示和小规格部署验证，不承诺生产 SLA。

---

## Q16：如果真的上线，下一步怎么做？

**回答：**

**P0（上线前必须）：**
1. 替换 Admin Token 为 JWT + 用户体系
2. 接入 Spring Security 与细粒度授权
3. 补齐集中日志、指标、告警和审计

**P1（进入开发前）：**
1. sources 字段脱敏（文件名映射为文档标题）
2. 日志脱敏（用户问题截断）
3. 在目标环境完成并发压测和容量基线

**P2（后续优化）：**
1. 多实例与外部向量数据库方案
2. Safety Guard 增强（变体关键词）
3. Prompt Injection 防护
4. 浏览器自动化 UAT 与更完整的 CI/CD

---

## Q17：这个项目你最大的技术难点是什么？

**回答：**

**Hybrid Retrieval 设计。**

Faiss 语义检索和 BM25 关键词检索的分数尺度不同，不能直接加权求和。我用 RRF（Reciprocal Rank Fusion）解决 — 只看排名不看分数，公式是 `1/(k+rank)`，不需要归一化。

难点在于：
1. BM25 自研字符级 n-gram 分词，不依赖 jieba，需要对中文友好
2. 三种检索模式（vector / hybrid / hybrid_rerank）需要统一接口
3. Query Rewrite 只改写检索用 query，不改变最终 prompt 中的用户问题
4. 评估体系需要验证每种模式的效果

---

## Q18：这个项目有哪些不足？

**回答：**

1. **评估集规模小** — 38 个 case，不能覆盖所有场景
2. **无正式认证** — Admin Token 是共享密钥，无 per-user 身份
3. **单机部署** — 无高可用、水平扩容和故障转移
4. **Safety Guard 仅关键词匹配** — 无法防变体绕过
5. **浏览器自动化不足** — 关键 UI 仍需要人工 UAT
6. **DTO 契约无强类型** — Java ↔ Python 手动对齐
7. **容量数据待补** — 已有有界并发和 k6 脚本，但服务器报告尚未归档

这些都是我在 Phase 3 审查中发现并记录的，有明确的修复计划。

---

## Q19：面试官问"这是不是生产级项目"怎么回答？

**回答：**

> 不是。当前有 HTTPS 公网 Demo，但定位是个人项目的功能演示和小规格部署验证，不承诺生产 SLA。
>
> 但我按生产化风险做了多轮安全加固 — Phase 3 共 12 项修复，覆盖 CORS、超时、输入校验、Safety Guard、traceId、异常收敛、Admin Token、Evaluation 访问限制。A3 QA 回归和 A4 安全复验都通过了。
>
> 部署边界已经收敛：Python 无宿主机端口，Java 只绑定 localhost，公网经过 Nginx；同时有入口限流和 Java/Python 双层有界并发。但认证、高可用、监控告警和大规模容量验证仍未完成。
>
> 所以我会把它描述为“已公网部署验证”，不会描述为“生产级系统”。

---

## Q20：你在多 Agent 协作中怎么控制质量？

**回答：**

我设计了一套多 Agent 协作框架：

1. **9 个协作文档** — 项目上下文、架构边界、API 契约、Agent 注册表、任务看板、交接模板、不可修改清单、发布检查清单、仪表盘
2. **Session 注册** — 每个 Agent 启动前注册会话，避免多会话同时修改同一模块
3. **分支管理** — 不在 main 分支直接开发，每个修复用 feature 分支
4. **合并检查** — 合并前必须通过 Smoke Test 和安全审查
5. **任务看板** — 每个任务有 Owner、分支、验收标准、状态
6. **A3/A4 复验** — 修复完成后由独立 Agent 做回归复验和安全复验

这套流程保证了 12 项修复的质量，A3 回归复验未发现退化。
