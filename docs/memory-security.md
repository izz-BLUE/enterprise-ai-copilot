# Scoped Conversation Memory Security Boundary

这份清单记录 Memory 的安全边界。Memory 是任务连续性层，不是身份系统、权限系统或业务数据库。

## Identity and scope

- [x] owner 只取 Java `VerifiedIdentity.userId()`。
- [x] Memory key 固定为 `(user_id, conversation_id)`。
- [x] `conversationId` 由 Java 校验，缺失时服务端生成；它不是身份凭证。
- [x] Python、LLM、前端 body、Tool arguments 和 Memory 内容不能覆盖 owner。
- [x] Java 生成 `employee_id`、`business_date`、`trace_id` 和 runtime thread；这些值不进入 LLM arguments。

## Read isolation

- [x] Read path 只读取 `status=ACTIVE`。
- [x] 终态 Memory 不 hydrate 到新请求。
- [x] `memoryContext` 在 Python 侧作为不可信历史，不覆盖 Runtime Context/capability。
- [x] `execution_history` 只有在 ACTIVE Memory + matching task type 时 hydrate。
- [x] 历史摘要标记为 `CONTEXT_ONLY`，不能成为当前业务事实、权限、PendingAction 查询源或金额依据。

## Trigger and write isolation

- [x] 现有 ACTIVE Memory 不会单独触发 Extractor。
- [x] Pure RAG、eval、余额/记录查询、travel/invoice read 不触发 Memory。
- [x] 只有 `action_proposal` 或白名单 Memory-eligible Tool success 才触发。
- [x] Python WritePolicy 只允许 `UPSERT + ACTIVE` proposal。
- [x] Python 不直接写 Java DB，不写 `COMPLETE`/`ABANDONED`/terminal Memory。
- [x] Java 当前认证请求负责落库、terminal transition、owner 和 conversation scope。
- [x] Memory proposal 不得绕过 PendingAction 直接产生 LeaveRequest/ExpenseClaim。

## Action and external safety

- [x] `confirmationNonce` 由 Java 生成，数据库只保存 digest。
- [x] Confirm 使用 owner、nonce、TTL、状态和 UUID idempotency key。
- [x] Stale confirm 收口为 Java Action FAILED、Memory ABANDONED、HITL REJECTED。
- [x] OA 不可用时保持 Pending，不伪造 terminal result。
- [x] WAITING_USER 与 WAITING_EXTERNAL 使用不同 marker、correlation 和 resume endpoint。
- [x] webhook body 不作为 OA status authority；Java HMAC 验证后仍 GET authoritative status。
- [x] Java ExpenseClaim terminal commit 先于 external resume；resume 失败不回滚业务终态。

## Operational boundary

默认策略：`MEMORY_WRITE_MODE=DISABLED`、`business.actions.enabled=false`、Mock OA provider disabled。外部 reconciliation/external-resume retry worker 仍按低频边界调度，但 provider 不可用时 fail-closed。打开功能必须同时理解 Java authority、数据库、内部 token、Checkpoint 和外部回调边界。

当前实现是小规格单机方案：Java/Python runtime guard 只在进程内生效，没有 distributed lease、event inbox/outbox、完整审计/告警或生产 SLA。任何需要多实例、真实 OA、强一致外部事务或长期 retention 的场景都需要单独的生产设计。
