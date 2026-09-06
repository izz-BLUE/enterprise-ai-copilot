# Memory 测试索引

> 本目录是**索引**，不是容器。
> 由于现有 `tests/test_memory_*.py` 文件已被 CI / IDE / 文档外部引用，
> 本索引只说明当前测试职责，不移动测试文件。

## 分类

### 1. contract（数据契约 / 跨端边界）

| 测试文件 | 覆盖范围 |
| --- | --- |
| `tests/test_memory_schema.py` | `MemoryProposal` / `MemoryExtractionInput` schema 校验 |
| `tests/test_memory_runtime_integration.py` | 响应内 Memory Proposal 集成契约 |

### 2. runtime（Memory Pipeline / Hook 行为）

| 测试文件 | 覆盖范围 |
| --- | --- |
| `tests/test_memory_audit.py` | `MemoryAuditEvent` 字段 + Recorder 行为 |
| `tests/test_memory_trigger_policy.py` | TriggerPolicy 启发式判定 |
| `tests/test_memory_extractor.py` | Extractor 输入 / 输出 / 解析失败 |
| `tests/test_memory_llm_adapter.py` | LLM Adapter 适配层 |
| `tests/test_memory_pipeline.py` | Pipeline 端到端 |
| `tests/test_memory_runtime_hook.py` | `MemoryRuntimeHook` 单元 |
| `tests/test_memory_runtime_integration.py` | Runtime Hook 集成（mock） |
| `tests/test_memory_write_dispatcher.py` | Dispatcher 行为 |
| `tests/test_memory_write_mode.py` | `MEMORY_WRITE_MODE` 切换 |
| `tests/test_memory_write_policy.py` | trusted 字段剥离 + 大小限制 |
| `tests/test_memory_context_read_path.py` | Read Path memoryContext 注入 |

### 3. evaluation（离线评估 / 度量 / 成本）

| 测试文件 | 覆盖范围 |
| --- | --- |
| `tests/test_memory_evaluation.py` | `MemoryEvaluator` 对照比较 |
| `tests/test_memory_case_loader.py` | YAML case 加载 / schema 校验 |
| `tests/test_memory_metrics.py` | `MemoryMetricsCollector` 聚合 |
| `tests/test_memory_cost_evaluation.py` | `MemoryCostEvaluator` 派生指标 |
| `tests/test_memory_release_evaluation.py` | Release Gate 五维判定 |

### 4. governance（生产保护 / 依赖审计）

| 测试文件 | 覆盖范围 |
| --- | --- |
| `tests/test_memory_rollout_policy.py` | 灰度策略确定性 |
| `tests/test_memory_quota_policy.py` | 写入状态机白名单 |
| `tests/test_memory_dependency_boundary.py` | 模块依赖审计 |

## 路径稳定性

- 所有测试文件继续位于 `tests/test_*.py`；
- pytest 收集路径与 CI 命令（`uv run pytest`）保持不变；
- 本索引是只读文档，不影响任何测试运行。

## 推荐验证路径

```bash
uv run pytest tests/test_memory_*.py
uv run pytest tests/test_memory_dependency_boundary.py
```
