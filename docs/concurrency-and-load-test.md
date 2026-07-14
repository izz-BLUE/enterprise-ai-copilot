# 并发保护与压测手册

## 目标与边界

本项目部署在小规格单机上，并发设计目标是**有界、可解释、可降级**，不是宣称高并发或生产级 SLA：

- 不让突发请求无限占用 Java 线程和 Python AI 执行资源
- 过载时在短队列截止时间后返回 HTTP 429，而不是继续堆积
- 保持成功请求和安全拒答的原有 JSON 结构
- 用分层压测区分 Nginx 限流、应用并发保护和真实 LLM 延迟

## 保护链路

| 层级 | 机制 | 默认值 | 作用 |
|------|------|--------|------|
| Nginx | 按客户端 IP 限流 | 2 req/s，burst 5 | 保护公网入口，超限返回 JSON 429 |
| Java | `PythonAgentBulkhead` | 3 个并发槽，排队 500ms | 限制 Java → Python 在途 AI 调用 |
| Python | `RequestConcurrencyLimiter` | 3 个并发槽，排队 500ms | 保护检索、Agent 和 LLM 执行入口 |
| Python LLM | 调用超时 | 30s | 限制模型调用时间 |
| Java | Python 读取超时 | 40s | 给 Python 清理和返回错误留出空间 |
| Nginx | 上游读取/发送超时 | 45s | 最外层超时预算 |

Java 和 Python 的健康接口都暴露不含敏感信息的并发快照：`maxConcurrent`、`active`、`available`、`rejected` 和 `queueTimeoutMs`。

应用层超载响应：

- HTTP 状态：`429 Too Many Requests`
- Header：`Retry-After: 1`
- `success=false`
- Agent 链路额外返回 `route=busy`、`category=overloaded`

公网 Nginx 限流发生在 Java 之前，因此使用 Nginx `$request_id` 作为边缘层 traceId；响应保持前端可解析的 `answer` / `model` / `traceId` / `success` JSON 结构，并包含 `Retry-After: 1`。它与进入 Java 后生成的 UUID traceId 属于不同层级。

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AI_MAX_CONCURRENT_REQUESTS` | `3` | Python AI 并发槽 |
| `AI_QUEUE_TIMEOUT_MS` | `500` | Python 获取槽位的最长等待时间 |
| `PYTHON_AGENT_MAX_CONCURRENT_REQUESTS` | `3` | Java 调 Python的并发槽 |
| `PYTHON_AGENT_ACQUIRE_TIMEOUT_MS` | `500` | Java 获取槽位的最长等待时间 |
| `LLM_TIMEOUT` | `30` | Python LLM 超时，单位秒 |
| `PYTHON_AGENT_CONNECT_TIMEOUT` | `3000` | Java 连接 Python 超时，单位毫秒 |
| `PYTHON_AGENT_READ_TIMEOUT` | `40000` | Java 读取 Python 超时，单位毫秒 |

两个并发上限应保持一致。当前默认值是针对 512 MiB Python / 512 MiB Java 的保守起点，必须用目标服务器实测后再调整。

## k6 脚本

脚本位于 `load-tests/k6/`。建议固定使用已经验证过的 k6 版本，并将原始输出和 `--summary-export` JSON 一并保存到测试记录中。

```bash
mkdir -p load-tests/results
```

`load-tests/results/` 已被 Git 忽略；确认口径后，只把脱敏汇总数据写入文档，不提交可能包含环境细节的原始输出。

### 1. 健康接口稳定性（零 LLM Token）

```bash
k6 run \
  -e BASE_URL=http://127.0.0.1:8080 \
  -e VUS=2 \
  -e DURATION=2m \
  --summary-export=load-tests/results/health-soak-summary.json \
  load-tests/k6/health-soak.js
```

### 2. Safety 全链路基线（零 LLM Token）

该脚本使用确定性高风险问题，Safety Guard 会在检索和 LLM 前拒答。

```bash
k6 run \
  -e BASE_URL=http://127.0.0.1:8080 \
  -e VUS=3 \
  -e ITERATIONS=30 \
  --summary-export=load-tests/results/safety-baseline-summary.json \
  load-tests/k6/safety-baseline.js
```

### 3. 应用层 AI 过载（少量真实 LLM Token）

必须从服务器 localhost 或可信内网运行，绕过公网 Nginx 的单 IP 限流，才能观察 Java/Python bulkhead。默认 6 VU、共 12 个请求；最坏情况下有 12 次 LLM 调用，实际被 429 拒绝的请求不会进入 LLM。

```bash
k6 run \
  -e BASE_URL=http://127.0.0.1:8080 \
  -e VUS=6 \
  -e ITERATIONS=12 \
  -e EXPECT_REJECTION=true \
  --summary-export=load-tests/results/ai-overload-summary.json \
  load-tests/k6/ai-overload.js
```

如只做功能基线、不要求必须出现 429，可传 `EXPECT_REJECTION=false`。不要在同一轮中同时提高 VU 和迭代次数。

### 4. 公网 Nginx 限流（零 LLM Token）

```bash
k6 run \
  -e BASE_URL=https://copilot.jintianchi.cn \
  -e RATE=10 \
  -e DURATION=5s \
  --summary-export=load-tests/results/public-rate-limit-summary.json \
  load-tests/k6/public-rate-limit.js
```

该脚本只访问健康接口，预期同时出现 200 和 429。429 必须是 JSON、包含 `Retry-After: 1` 和非空 traceId，且不得暴露具体 Nginx 版本。它验证的是公网入口限流，不代表应用 bulkhead 容量。

## 执行顺序与观测

每轮按以下顺序执行，并在测试前后记录：

1. `docker ps`、`docker stats --no-stream`、`free -h`、`swapon --show`
2. Java/Python 健康接口及并发快照
3. k6 原始输出、P50/P95/P99、200/429/5xx 数量
4. Java/Python `RestartCount`、容器日志中的 timeout/OOM/429
5. eat-what 与 jobfit 的既有健康接口

停止条件：

- 任一 EAC、eat-what 或 jobfit 容器异常重启或健康检查失败
- 出现 OOM、持续 5xx、线程/连接耗尽
- 系统可用内存低于 512 MiB，或 Swap 在单轮中增长超过 512 MiB
- LLM 成功请求 P95 超过 40s
- 实际 LLM 请求数超过本轮预设迭代数

## 验收口径

| 场景 | 最低验收条件 |
|------|--------------|
| 健康稳定性 | 0 失败，P95 < 1s |
| Safety 基线 | 100% HTTP 200，全部 `route=refuse`，traceId 一致，P95 < 3s |
| AI 过载 | 仅出现有效 200 或显式 429；无 5xx；429 有 `Retry-After`；服务无重启 |
| 公网限流 | 出现 200 与 429；429 契约检查全部通过；无 5xx；依赖服务正常 |

目标服务器已完成 L1-L4 受控验收，脱敏摘要见 [`performance.md`](performance.md#目标服务器受控并发压测)。这些结果证明保护机制和测试方法在当前小规格服务器上有效，不等于获得了大规模容量或生产 SLA 结论；引用时必须同时说明测试层级、持续时间和环境边界。
