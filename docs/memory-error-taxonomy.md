# Memory P0 Error Taxonomy

本表只描述当前实现的错误边界和发布判断，不引入 retry、fallback 或新的 Runtime 行为。
“可重试”是运维分类，不代表当前代码会自动重试；当前 Memory Java Client / Dispatcher
明确不做 retry / fallback。

> **归档状态（本文档更新时点）**：末尾"与 Release Gate 的关系"一节引用的
> `MemoryReleaseEvaluator` 已随 v1 治理组件归档至 `archive/memory-v1/`
> （见 `docs/memory-p0-change-log.md` 第 2 节）；`memory_metrics.py` 同样已归档。
> 本文档保留为 Phase 6 冻结基线，当前运行时错误分类（四类核心错误表）仍适用。

## 分类定义

- **expected failure**：契约内、可预期的拒绝或降级，允许按 fail-safe noop 处理。
- **retryable**：只有底层原因属于暂时性网络 / 服务不可用时才具备重试价值；4xx、schema、
  trusted scope 或状态错误不可盲目重试。
- **release blocker**：发生后不能直接把 Memory 推荐为 ENABLED；需要修复、重跑评估或人工确认。

## 四类核心错误

| Error | 产生位置 / 触发条件 | expected failure | retryable | release blocker | 当前处理 |
| --- | --- | --- | --- | --- | --- |
| `MemoryExtractionParseError` | LLM 输出不是 JSON object、字段非法、extra 字段或 Pydantic 校验失败 | **是**。这是 Extractor 的合法失败信号 | **否**。当前按 noop 处理；若未来重试 LLM，必须由上层显式策略决定 | **通常否（单次）**；但若被聚合为 release error signal，必须按 Release Gate 规则复核 | Pipeline 丢弃 proposal，不调用写入，Runtime 不被阻断 |
| `MemoryPipelineError` | Pipeline 输入契约错误、组件非预期异常或调度 bug | **否** | **否**。先修复或定位根因，不能靠重试掩盖代码错误 | **是**。表示 Pipeline 边界异常，禁止直接 ENABLED | 保留 cause，由 Runtime Hook 记录并 fail-safe 返回主响应 |
| `MemoryWriteDispatcherError` | Dispatcher 注入的 writer 抛出异常，或 writer 调度失败 | **否**（健康写入路径不应出现） | **条件可重试**：仅当底层原因确认是暂时性服务 / 网络故障；当前 Dispatcher 不自动重试 | **是**。写失败会进入 audit / metrics，Release Gate 对写失败 fail-closed | 包装并保留原始异常链，Runtime Hook 记录 `write_success=False` |
| `JavaMemoryClientError` | HTTP client 抛异常，或 Java 返回 HTTP ≥ 400 | **按原因区分**：Java 4xx 是预期拒绝信号，成功写入路径不是 | **条件可重试**：超时、连接失败、5xx 可由外部运维策略评估；400/403 scope、token、payload 不可重试 | **是**（对 ENABLED 写入）：任一未解释的 Java 写失败都阻断发布建议 | Client 统一包装；不读取业务 response body，不做 retry / fallback |

## Java 返回错误的细分

| HTTP / error code | 分类 | 处理建议 |
| --- | --- | --- |
| 400 `MEMORY_PAYLOAD_INVALID` | expected rejection，非 retryable | 检查 Python Command 与 Java DTO / 状态机契约 |
| 400 `MEMORY_TRUSTED_KEY_REJECTED` | expected security rejection，非 retryable | 保持阻断，不放宽过滤；检查输入是否污染 |
| 400 `MEMORY_CONVERSATION_ID_INVALID` | expected rejection，非 retryable | 检查服务端 conversation scope/path，不从客户端扩大信任 |
| 403 `MEMORY_INTERNAL_TOKEN_REQUIRED` | configuration / auth failure，非 retryable | 补齐或轮换服务配置后重新验证 |
| 403 `MEMORY_SCOPE_INVALID` / `MEMORY_SCOPE_MISMATCH` | security rejection，非 retryable | 检查 scope TTL、签名、owner 和 path 绑定 |
| 500 `MEMORY_INTERNAL_ERROR` | unexpected downstream failure | 需人工定位；只有确认暂时性基础设施故障时才考虑外部重试 |

## 与 Release Gate 的关系

> 本节约定的 Release Gate fail-closed 语义已随 `MemoryReleaseEvaluator` 归档至
> `archive/memory-v1/`；当前运行时不做聚合发布判定。保留本节作为冻结基线。

当前 `MemoryReleaseEvaluator` 对 `write_failure_total > 0` 和非空 `error_counts` 采用
fail-closed。因而：

1. Runtime 的“单次 expected parse noop”不应被等同为 Java 写入失败；
2. 聚合层若记录了 parse error，发布报告必须能区分 expected parse 与 Pipeline / write error；
3. 在没有这种区分证据前，任何非空 error 聚合都只能保持 BLOCKED，不得为了 READY 而删除或降级错误。

## 证据位置

- Python：`agent-python/app/memory/memory_extractor.py`、`memory_pipeline.py`、
  `memory_write_dispatcher.py`、`agent-python/app/clients/java_memory_client.py`
- Runtime audit / metrics：`agent-python/app/memory/memory_runtime_hook.py`、`memory_audit.py`、
  `memory_metrics.py`
- Java：`backend-java/src/main/java/com/fantuan/copilot/service/memory/MemoryWriteException.java`
  与 `controller/memory/MemoryWriteController.java`
