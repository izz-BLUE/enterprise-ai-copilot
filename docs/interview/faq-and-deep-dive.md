# Interview FAQ and Deep Dive

回答都以当前实现为边界，不把小规格 Demo、Mock OA 或 fixture-backed MCP 说成生产系统。

## Q1：项目解决什么问题？

企业员工需要检索制度，也会提出需要确认的请假/报销请求。项目把可追溯 RAG 与受控业务流程放在一个 Java authority + Python Agent 架构中。

## Q2：为什么 Java + Python 双服务？

Java 保留认证、权限、事务和业务系统边界；Python 利用 FAISS、LLM、LangGraph 等 AI 生态。代价是 HTTP contract、超时、部署和跨服务状态需要额外设计。

## Q3：Java 和 Python 谁是 authority？

Java 是身份、权限、PendingAction、LeaveRequest、ExpenseClaim、Memory lifecycle 和外部状态落库的 authority。Python 可以规划、检索和返回 Proposal，但不能授权或直接写业务库。

## Q4：RAG 链路是什么？

文档切 chunk 和 metadata，使用 BGE embedding + FAISS 语义召回，再用字符 BM25 做关键词召回，最后 RRF 融合，必要时再做 Cross Encoder 实验，生成返回 sources。

## Q5：为什么使用 RRF？

FAISS 与 BM25 的分数尺度不一致，直接相加需要不稳定的人工归一化。RRF 只融合排名，结构简单、可解释，适合当前小型领域数据集。

## Q6：如何控制幻觉？

Prompt 要求基于检索证据回答，无证据时明确拒答；返回 source metadata；通过 38 个 case 分别评估 source hit、keyword hit、生成关键词和 no-answer。它不是完整事实保证。

## Q7：Planner-first 与 Router-first 的关系？

生产入口固定是 `safety → planner ⇄ tool_executor → finalize`；`safety → router → rag|eval|action|refuse` 仅作为测试/离线兼容图。两套图共享 Java authority；Router-first 不是生产 primary。

## Q8：Planner 会不会直接执行报销？

不会。Planner 只能输出严格 decision，Tool Executor 还会检查 capability、employee、budget 和 dedupe。`expense_proposal_tool` 只能生成 Proposal，Java PendingAction + Confirm 才能打开业务写事务。

## Q9：Tool 权限如何限制？

程序根据可信 Runtime Context 和配置计算 visible tools，模型不能自行增加 Tool。Executor 在执行前再次校验 schema、身份、权限、最多 5 次 Tool execution 和成功签名；系统字段不允许出现在 LLM arguments。

## Q10：为什么需要 PendingAction？

它把“模型建议”与“业务授权”分开，持久化 owner、action type、nonce digest、TTL、status 和 correlation。Confirm 时服务端重新校验，避免只相信浏览器或旧 Proposal。

## Q11：nonce 如何防止伪造？

Java 用 SecureRandom 生成 nonce，明文只返回一次，数据库只保存 SHA-256 digest；Confirm 需要当前 owner、nonce、TTL 和状态。前端只把明文放在当前页面内存，不放 URL、localStorage 或日志。

## Q12：幂等怎么做？

Confirm 要求 UUID `Idempotency-Key`，Java 对 Action 行加锁并持久化结果；相同或新的合法 key 对终态 Action 都重放同一 requestId。Expense 对 Mock OA 使用 `expense:<expenseId>` key，防止重复创建外部 approval。

## Q13：confirm-time revalidation 解决什么？

Proposal 生成和用户点击 Confirm 之间，trip/invoice 可能变化。Java 在本地写事务外调用 Python narrow adapter，重新检查 trip ownership/status/current dates、invoice ownership/valid/duplicate/amount/category，并由 Java 重算金额。

## Q14：重校验发现 stale 怎么办？

Action 变为 FAILED，Memory 变为 ABANDONED，HITL 以 REJECTED 收口并 resume 到 Graph END，不创建 ExpenseClaim。若 Java stale 终态已提交但 Python resume 暂时不可用，Java FAILED 不回滚；重复 Confirm 不重新查询 OA 或改变 Java 状态，只重试同一个确定性的 REJECTED continuation，且没有 autonomous stale-HITL worker。远程 OA 不可用则保留 PENDING_CONFIRMATION 并返回 503，允许重试；两次远程读取与本地 commit 之间的小型 TOCTOU 窗口是明确接受的限制。

## Q15：WAITING_USER 和 WAITING_EXTERNAL 有什么区别？

WAITING_USER 是等用户是否确认 PendingAction；WAITING_EXTERNAL 是 ExpenseClaim 已提交后等 OA 决定。二者使用不同 marker、correlation、resume endpoint 和失败语义，普通 Chat 不能跨过 active wait。

## Q16：Mock OA 为什么不直接信 webhook status？

Mock OA webhook 故意不带 status，只带 event/request correlation。Java 先验证 raw-body HMAC 和 300 秒 timestamp，再 GET OA authoritative status；因此重放、篡改或乱序通知不能直接伪造业务状态。

## Q17：webhook 丢失怎么办？

默认关闭的 reconciliation 只扫描 `WAITING_APPROVAL + MOCK_OA + external_request_id`，对 `external_last_checked_at` 做 due CAS 后在事务外 GET，并与 webhook 共用 status-sync service。它是低频、限批补偿，不是消息队列。

## Q18：外部 resume 失败会不会回滚报销？

不会。Java 先提交 ExpenseClaim APPROVED/REJECTED 终态，再调用 Python external resume。失败只记录 `external_resume_last_attempt_at`，未完成时由 bounded retry 重新投递；Python 支持 `WAITING_EXTERNAL`、continuation 和 completed no-op。

## Q19：Checkpoint 和 Memory 的区别？

Checkpoint 是 runtime execution scene，用于 crash/HITL/external resume；Memory 是 `(user_id, conversation_id)` 下的 ACTIVE task continuity，由 Java 管 lifecycle。Checkpoint 不是权限或业务事实，Memory 也不能替代 Java DB。

## Q20：execution_history 和 tool_history 的区别？

`tool_history` 是本次 execution 的真实调用和去重依据，新请求清空；`execution_history` 是有界成功摘要，只在 ACTIVE Memory + task type 匹配时 hydrate，且是 `CONTEXT_ONLY`，不能用于金额、当前事实、Tool dedupe 或 Memory trigger。

## Q21：ACTIVE Memory 会触发新的 Memory 写入吗？

不会。现有 ACTIVE Memory 只是 read context。Trigger 只来自 `action_proposal` 或白名单 Memory-eligible Tool success；纯 RAG、eval、余额、记录查询、MCP read、失败和拒答都不触发。

## Q22：Python 能不能结束 Memory？

不能。Python write policy 只返回 `UPSERT + ACTIVE` proposal；Java 在当前认证上下文中落库，并在 Confirm/Cancel/Expire/Stale/Failure 时控制 terminal lifecycle。这样避免 untrusted Agent output 结束业务任务。

## Q23：为什么没有直接用 Temporal/DBOS？

当前目标是小规格单机和已有 PostgreSQL 上验证 graph state、HITL 和外部恢复语义。引入工作流引擎会扩大运维和状态迁移范围；生产多实例需要重新评估，而不是宣称 Checkpoint 已等价替代。

## Q24：为什么不用 Kafka 或 distributed lock？

当前没有多实例部署，也没有消息总线需求；process-local guard 足够保护单实例同一 runtime thread 的 lifecycle。多实例、跨进程投递和故障转移是下一阶段，届时先需要 distributed execution lease/ownership；只有选择 durable event delivery 时才需要评估 Outbox/Inbox 和重复消费策略。

## Q25：当前是不是生产级？

不是。当前是小规格单机、短时受控验证，有 Mock OA、fixture-backed MCP、有限 eval/容量数据，没有真实生产 credentials、正式 OA 分布式事务、完整 metrics/alerting 或 SLA。

## Q26：怎么证明项目质量？

接受基线为 Java 334、Python 1402 + 34 expected skips、PostgreSQL durable flows 34 passed/0 skipped、Enterprise OA MCP 24、Mock OA 17、Frontend 44，另有 lint/build pass；CI 分别覆盖 backend、Mock OA、Python eval、frontend、browser、Gitleaks、CodeQL。

## Q27：Safety Guard 能解决 Prompt Injection 吗？

不能完全解决。当前是规则型纵深防御，能拦截已知风险词和安全类别，不等价于完整的 Prompt Injection、内容安全或模型对齐系统；这是接受限制。

## Q28：如果真实上线，优先补什么？

先补正式身份/RBAC、真实 OA 的 provider-side version/CAS/幂等/补偿/状态映射和分布式 execution lease；若需要可靠 after-commit event delivery，再评估 Outbox；再补集中 metrics、审计、SLO、长时容量和故障演练。最后扩大安全模型、租户隔离和检索评估集。

## Q29：最难的技术点是什么？

不是单个 LLM API，而是跨服务、跨时间的状态语义：用户确认与外部审批必须区分，Java 终态必须先提交，webhook 不能当 status authority，resume 丢失要可重试且不能重复写业务。

## Q30：这个项目最大的取舍是什么？

用更清楚的 Java/Python authority 边界、PostgresSaver 和 Mock OA 低成本验证复杂流程，换取当前没有分布式工作流、真实 OA 事务和生产级运营能力。文档把这些边界列为 accepted limitations，而不是隐藏起来。
