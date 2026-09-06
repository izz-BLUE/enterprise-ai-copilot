# 性能

## 问题背景

原 Sentence Transformers 路径存在内存问题：

| 后端 | 说明 | 内存影响 |
|------|------|----------|
| Torch Backend | SentenceTransformer + PyTorch | 高（加载完整 Torch） |
| ONNX_ST Backend | SentenceTransformer ONNX | 更高（仍加载 Torch + ONNX Runtime） |

Sentence Transformers 的 ONNX 后端仍然加载 Torch，再额外加载 ONNX Runtime，因此内存不降反升。

## 解决方案：Direct ONNX Runtime

独立实现 ONNX 推理模块，不依赖 sentence-transformers、torch 或 optimum：

- 使用 `onnxruntime.InferenceSession` 直接加载 ONNX 模型
- 使用 `tokenizers.Tokenizer` 加载分词器
- 从 `1_Pooling/config.json` 读取池化配置
- 支持 CLS/Mean/Max 池化模式

## 后端内存对比

以下为 Linux 独立进程基准测试结果：

| 后端 | 最大 RSS | 说明 |
|------|----------|------|
| Torch | 876.7 MB | SentenceTransformer + PyTorch |
| ONNX_ST | 920.1 MB | SentenceTransformer + ONNX Runtime，仍加载 Torch |
| Direct ONNX | 174.2 MB | 独立进程基准，不加载 Torch |

> 注意：这些是独立进程基准结果，不包含 FAISS、BM25 等其他组件。

## 容器 Smoke 测试

完整服务容器测试（包含 FAISS、BM25、Chunks）：

| 指标 | 结果 |
|------|------|
| 测试内容 | 50 次 Retrieval |
| 最大内存 | 约 224.9 MiB |
| OOM | 无 |
| 重启 | 无 |

## 腾讯云隔离运行

完整服务在腾讯云小规格实例上的 30 分钟观察：

| 指标 | Python | Java |
|------|--------|------|
| 稳态内存 | 约 330 MiB | 约 160 MiB |
| 容器限制 | 512 MiB | 512 MiB |
| OOM | 无 | 无 |
| 重启 | 无 | 无 |

> 注意：重启后显示的 95 MiB 是冷启动状态，不能替代完整负载数据。

## 公网演示发布后观察

公网演示（https://copilot.jintianchi.cn）发布后 30 分钟观察：

**测试口径：**
- 观察时间：30 分钟
- 完成公网前端加载、健康检查和至少一次完整 LLM 问答
- 非并发压测

**资源指标：**

| 指标 | Python | Java | 系统 |
|------|--------|------|------|
| 内存使用 | 约 296 MiB / 512 MiB | 约 195 MiB / 512 MiB | 约 830 MiB 可用 |
| Swap 使用 | - | - | 约 8 MiB |
| OOM | 无 | 无 | - |
| 重启 | 无 | 无 | - |
| RestartCount | 0 | 0 | - |

**公网验证：**
- HTTPS 首页：200
- /api/health：UP
- /api/agent/health：UP
- 完整问答：success=true，traceId 正常
- 无 5xx 错误
- 无 OOM 或持续异常

**重要说明：**
- 这不是并发压测，不代表长期峰值
- 不替代历史完整负载约 330 MiB 的保守容量口径
- 公网访问仍不等于大规模生产性能验证

## 向量一致性

Torch vs Direct ONNX 向量对比：

| 指标 | 结果 |
|------|------|
| cosine 最小值 | 0.999999880791 |
| max_abs_error | 2.452871e-07 |
| 维度 | 512 |
| NaN/Inf | 无 |

## 检索一致性

Top-K 检索结果对比：

| 指标 | 结果 |
|------|------|
| Top1 匹配 | 38/38 |
| Top3 集合匹配 | 38/38 |
| 变化 case | 0 |

## 检索评估

### none 模式（默认）

| 指标 | 结果 |
|------|------|
| source_hit_rate | 100% |
| keyword_hit_rate | 96.4% |
| final_pass_rate | 96.4% |

### rule 模式（实验）

| 指标 | 结果 |
|------|------|
| source_hit_rate | 100% |
| keyword_hit_rate | 100% |
| final_pass_rate | 100% |

## 目标服务器受控并发压测

2026-07-14 在公网演示所在的 3.3 GiB 共享服务器上完成分层受控压测。EAC Java/Python 容器内存限制均为 512 MiB，Java 和 Python 并发槽均配置为 3，候选版本为 `2daa507`。原始 k6 输出不提交 Git；下表仅归档脱敏验收摘要。

| 层级 | 场景 | 请求结果 | 延迟 | 验收结论 |
|------|------|----------|------|----------|
| L1 | localhost 健康接口，2 VU / 2 分钟 | 560,336 次，失败 0，5xx 0 | P95 0.468 ms，最大 13.17 ms | 通过 |
| L2 | Safety Guard，3 VU / 30 次 | 30/30 返回确定性拒答，5xx 0 | P95 117 ms，最大 121 ms | 通过 |
| L3 | AI 过载，6 VU / 12 次 | 4 次成功、8 次 429、5xx 0 | P95 1.67 s，最大 1.70 s | Java bulkhead 拒绝计数与 k6 完全一致 |
| L4 | 公网 Nginx 限流，10 req/s / 5 秒 | 15 次 200、36 次 429、5xx 0 | P95 4.02 ms，P99 4.13 ms | JSON 429 契约全部通过 |

L4 验证的公网 429 契约包括 `Content-Type: application/json`、`Retry-After: 1`、非空 traceId、`success=false`，且响应头和响应体均不暴露具体 Nginx 版本。该轮从服务器访问公网 HTTPS 域名，能够验证 Nginx 入口行为，但不代表外部互联网链路延迟。

**资源与稳定性：**

- Java/Python `RestartCount` 始终为 0，无 OOM、无 5xx
- L3 后系统可用内存约 920 MiB
- Swap 从约 125 MiB 增至 143 MiB（+18 MiB），L4 后无继续增长
- eat-what 与 jobfit 在测试前后均保持健康
- L3 实际进入 LLM 的请求为 4 次，其余 8 次在 Java 层快速返回 429

**结果边界：**

- L1 脚本没有请求间隔，560,336 次是 localhost 健康接口的紧循环结果，不能表述为公网 QPS 或业务容量
- L3 证明的是“配置为 3 的并发边界能够生效并快速拒绝过载”，不是系统最大吞吐量
- L4 证明的是单客户端 IP 的 Nginx 限流与错误响应契约，不代表多客户端容量
- 测试持续时间较短，不能替代长时间 Soak、分布式压测或生产 SLA

## 结论边界

- 测试数据规模较小（当前知识库为 HR、Finance、IT 的小型 Synthetic Demo Corpus）
- 结果不代表大规模生产负载
- 结果证明的是当前数据和部署环境下的兼容性及资源可行性
- 已加入有界并发保护和 k6 分层压测脚本，并完成目标服务器 L1-L4 受控验收
- 未宣称具备大规模企业生产性能

压测前置条件、脚本、停止条件与结果引用规则见 [`concurrency-and-load-test.md`](concurrency-and-load-test.md)。引用指标时必须同时说明测试层级、短时测试边界和共享服务器规格，不把健康接口结果包装成业务 QPS。
