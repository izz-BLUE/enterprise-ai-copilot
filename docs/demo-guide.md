# 多用户请假 Demo Guide

## 启动配置

在隔离的本地或受控演示环境设置：

```text
DEMO_IDENTITY_ENABLED=true
BUSINESS_ACTIONS_ENABLED=true
BUSINESS_ACTIONS_REQUIRE_ADMIN=true
```

同时配置 PostgreSQL 和 Admin Token，但不要在终端、截图或演示话术中展示其值。`X-Demo-User-Id` 不是登录或认证，不能用于公开生产环境。

## 演示顺序

1. 选择 **Demo User**，生成请假草稿并 Confirm，说明 Action 绑定 `DEMO-001`，只扣该账户余额。
2. 选择 **Demo User B**，申请与 User A 相同日期并 Confirm，说明冲突查询按 employeeId 隔离。
3. 生成一份草稿后选择 Cancel，说明 nonce 只在页面内存，取消不发送 Idempotency-Key。
4. 用隔离测试展示 User B 即使持有 User A 的 actionId 和 nonce，也只得到 `ACTION_NOT_FOUND`；User A 随后仍可操作。
5. 选择 **Demo Manager** 创建自己的草稿，并说明 Manager 当前没有审批、查看或操作他人申请的权限。
6. User A 创建 Pending 草稿后重启 Java；用 User B 操作仍被拒绝，User A 可继续 Confirm，展示 PostgreSQL 恢复能力。

## 讲解话术

“模型只发零参数 Tool 信号，身份不会进入 Python 或模型。Java 从固定白名单请求头解析 Demo 身份，把草稿、余额和申请绑定到 employeeId。Confirm/Cancel 先锁 Action、校验 owner，再验证 nonce 和状态；跨用户与不存在 Action 返回相同结果，避免探测。最终写入通过 `LeaveExecutionGateway`，当前实现仍是 PostgreSQL Sandbox。”

## 真实 OA 边界

当前 `PostgresLeaveSandboxGateway` 能参加本地 PostgreSQL 事务。真实 OA 网络调用不能加入该事务，未来需要 Transactional Outbox、异步投递、外部请求幂等、回调或轮询、重试、对账、补偿和状态映射。本项目没有发送任何真实 OA 请求。
