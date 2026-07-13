# Performance

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

## 容器 Smoke Test

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

## Retrieval Evaluation

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

## 结论边界

- 测试数据规模较小（当前知识库约 33 chunks）
- 结果不代表大规模生产负载
- 结果证明的是当前数据和部署环境下的兼容性及资源可行性
- 未执行高并发压测
- 未宣称具备大规模企业生产性能
