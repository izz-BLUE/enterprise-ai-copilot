# Roadmap

路线图只保留真正尚未完成的工作。已经在当前代码和验收基线中实现的能力列在“已完成”，不再以未来计划措辞重复描述。

## 已完成

- Java Spring Boot + Python FastAPI + React 三端链路；
- Java authority boundary：JWT DemoAuth、Admin gate、trace、超时、有界并发和稳定错误响应；
- RAG：chunking、BGE embedding、FAISS、字符 BM25、RRF、sources 和 38-case evaluation；
- Planner-first LangGraph，以及 `AGENT_LOOP_ENABLED=false` 的 Router-first 兼容回退；
- strict PlannerDecision、动态 Tool capability gate、Tool budget 和成功签名去重；
- `ANNUAL_LEAVE_REQUEST` 与 `EXPENSE_CLAIM` Proposal；
- Java PendingAction：nonce digest、owner、TTL、幂等、事务、状态机和 Memory terminal lifecycle；
- Expense Claim 与 Expense Item 持久化、确定性金额计算和 confirm-time revalidation；
- PostgreSQL `PostgresSaver` execution checkpoint、crash recovery、`graph.invoke(None)`；
- 独立的 `WAITING_USER` 与 `WAITING_EXTERNAL`，Java-authoritative `Command(resume)`；
- Mock OA SQLite、PENDING→APPROVED/REJECTED、HMAC webhook、authoritative GET、bounded reconciliation 和 external resume retry markers；
- Scoped Conversation Memory：ACTIVE read、trigger/extractor/write policy、Java owner/lifecycle；
- Enterprise OA MCP read-only travel/invoice integration；
- React confirmation UI、Playwright browser baseline、CI、Gitleaks、CodeQL 和 Dependabot；
- 小规格单机部署、短时受控验证和最终项目/面试文档。

## 下一阶段优先级

以下项目尚未完成，不能在当前系统上宣称已具备：

1. **生产身份与授权**：将演示/最小 Admin Token 口径替换为正式用户目录、细粒度 RBAC/ABAC、审计和密钥轮换。
2. **真实 OA 集成**：接入具备版本/ETag/CAS/幂等契约的真实 OA，设计 provider-side precondition/version handling、重试、补偿、状态映射和对账；若需要本地事务后的可靠 command/event delivery，再评估 Transactional Outbox。Mock OA 不作为生产替代。
3. **分布式运行**：为多 Java/Python 实例增加 distributed execution ownership/lease、故障转移和必要的任务投递；只有选择 durable event delivery 时再评估 Outbox/Inbox，当前 process-local guard 不足以支撑水平扩展。
4. **可观测性运营化**：集中 metrics、日志、告警、SLO/SLA、审计保留和敏感字段治理；Phoenix 当前只是可选旁路 trace。
5. **容量与恢复工程**：长时间、多客户端、跨实例压测，Checkpoint retention/pruning，故障演练和容量模型。
6. **安全增强**：Prompt Injection、内容安全模型、规则变体绕过、MCP 服务身份和更完整的输入/输出数据分级。
7. **数据与检索规模化**：多租户隔离、文档上传/版本、增量索引、外部向量存储和更大领域评估集。

## 明确不作为当前默认方案

Temporal、DBOS、Kafka、Redis、分布式锁、真实 OA 审批引擎和“把 Memory 当成用户画像”都不在当前实现中。它们可以在生产设计阶段评估，但不能通过文档措辞提前宣称已经接入。

## 规划原则

- 先保护 Java authority 和数据权限，再扩大 Agent capability；
- 任何跨服务状态都必须有明确 owner、correlation、幂等和失败语义；
- 外部副作用必须可重试、可对账、可观测；
- 新能力需要同步 API、架构、部署、测试和 interview material；
- 受控动作和 Memory 的语义必须继续分离。
