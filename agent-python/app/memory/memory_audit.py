"""memory_audit.py —— Memory Audit / Observability 层（Phase 4D / Phase 8-A 修订）

职责：
  Memory Write Path 的可观测性边界。在真实开启 Memory Write 前，
  观察 Memory 行为（触发 / 提案 / 写入结果 / 错误），用于：
    - 评估 Trigger Policy / Extractor 的命中率与误触发率；
    - 监控 Dispatcher / Java Memory Client 的写入成功率；
    - 出错事件留给运维 / 告警系统消费。

边界与不变式：
  1. **Privacy boundary（最重要）**：AuditEvent 绝不包含任何敏感 / 业务数据。
     - 禁止字段：user_id / employee_id / conversation_id / token / jwt /
       summary / task_state 等；
     - 只记录元数据（是否触发 / action 类型 / 任务类型 / 写入是否成功 /
       错误类型 / Rollout Mode / Resolution Reason / Failure Category）。
     - 错误类型只记异常类名，不泄漏 message（message 可能含敏感内容）。
  2. **Fail-safe boundary**：Recorder 失败绝不阻断 Memory Pipeline / Agent 响应。
     - Recorder 抛任何 RuntimeError → 仅日志告警。
     - Audit 阶段不修改 MemoryPipelineResult / MemoryRuntimeResult；
       仅在最后调用一次 recorder.record(event)。
  3. **P0 / Phase 8 阶段**：
     - 默认实现：LoggingAuditRecorder（写入 logger，in-memory 累计）；
     - 不引入 DB / Kafka / metrics server / LangSmith 上报；
     - 注入位留给未来生产实现。

Phase 8-A 修订（Observability Review）：

  增加可选诊断字段（**仍然全部是非业务敏感元数据**）：

    memory_write_mode       —— 当前 MemoryWriteExecutionPolicy.mode 取值
                                （'DISABLED' / 'AUDIT_ONLY' / 'ENABLED'），
                                用于监控"灰度放量进度"与"哪条模式生效"。
    memory_resolution_reason —— MemoryTaskResolutionPolicy 的 decision.reason
                                字串（仅在 Read Path 集成后填入；当前 Hook
                                范围下保留 None / 空字符串）。
    failure_category        —— 失败类别（'pipeline_error' / 'dispatcher_error'
                                / 'audit_error' / 'invalid_snapshot' / None），
                                用于按类别聚合告警。

  **仍然禁止**：
    - user_id / employee_id / conversation_id / token / jwt / summary /
      task_state / proposal / 任何业务字段；
    - 完整 task_state / full summary 序列化；
    - 任何身份 / 用户标识。

非职责（明确不做）：
  - 不读 agent_result / 不读 proposal.summary / 不读 task_state；
  - 不修改 MemoryPipeline / Dispatcher / JavaMemoryClient 核心控制流；
  - 不修改 LangGraph / AgentState / Planner / Tool Executor。
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants（Phase 8-A 引入；非业务敏感诊断常量）
# ---------------------------------------------------------------------------

# Rollout Mode 字面量（与 MemoryWriteExecutionPolicy.mode 对齐）。
MemoryWriteModeLiteral = Literal['DISABLED', 'AUDIT_ONLY', 'ENABLED']

# Failure Category 字面量；用于按类别聚合告警。
FailureCategoryLiteral = Literal[
    'pipeline_error',         # Pipeline 调度失败（MemoryPipelineError 等）
    'dispatcher_error',       # Dispatcher / Java Client 失败
    'audit_error',            # Audit Recorder 自身失败（极少）
    'extractor_parse_error',  # LLM 输出非法 JSON / 字段校验失败（已降级 noop）
    'invalid_snapshot',       # task_state 包含禁止键 / 序列化超限
    'unknown',                # 兜底
]


# ---------------------------------------------------------------------------
# Event schema
# ---------------------------------------------------------------------------


class MemoryAuditEvent(BaseModel):
    """Memory Write Path 单次执行的审计事件。

    字段语义：
      triggered                —— Pipeline 是否判定"值得调用"。
      trigger_reason           —— TriggerPolicy 的 reason 字符串（来自 MemoryPipelineResult）。
      proposal_action          —— Extractor 产出的 MemoryProposal.action；Pipeline 失败时为 None。
      task_type                —— Extractor 产出的 MemoryProposal.task_type；同上 None。
      write_attempted          —— 是否实际调用了 Dispatcher（即 triggered + command 存在 + mode=ENABLED）。
      write_success            —— Dispatcher 是否成功返回（write_attempted=False 时恒为 False）。
      error_type               —— 错误事件类型（异常类名 / "pipeline_error" / "dispatcher_error" /
                                  "audit_error"）；成功路径为 None。
      memory_write_mode        —— 当前 Rollout Mode（Phase 8-A 新增）；用于监控放量进度。
      memory_resolution_reason —— Read Path 集成后填入 Resolution reason；当前 Hook 范围恒为 None。
      failure_category         —— 失败类别（Phase 8-A 新增）；用于按类别聚合告警。

    隐私边界（extra='forbid' + 字段白名单）：
      禁止字段：user_id / employee_id / conversation_id / token / jwt /
                summary / task_state / proposal / 其他任何业务字段。

    Phase 8-A 新增字段均为非业务敏感诊断元数据，不携带身份 / 业务 / 任务上下文。
    """

    model_config = ConfigDict(extra='forbid')

    triggered: bool
    trigger_reason: str = ''
    proposal_action: str | None = None
    task_type: str | None = None
    write_attempted: bool = False
    write_success: bool = False
    error_type: str | None = None
    memory_write_mode: MemoryWriteModeLiteral | None = None
    memory_resolution_reason: str = ''
    failure_category: FailureCategoryLiteral | None = None


# ---------------------------------------------------------------------------
# Recorder abstract
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryAuditRecorder(Protocol):
    """Audit recorder 抽象协议。

    实现契约：
      - record(event) 接收 MemoryAuditEvent；
      - 抛出任何 RuntimeError 时由 Hook 捕获并仅记日志，绝不冒泡。

    注入位：MemoryRuntimeHook 接受 MemoryAuditRecorder；默认 LoggingAuditRecorder。
    """

    def record(self, event: MemoryAuditEvent) -> None:
        ...


# ---------------------------------------------------------------------------
# Default P0 recorder
# ---------------------------------------------------------------------------


class LoggingAuditRecorder:
    """P0 默认 Audit Recorder：写日志 + 内存累计。

    行为：
      - record(event) → logger.info 输出 JSON-like 行（无业务敏感字段）；
      - 累计 events 列表（仅在 P0 评估 / 调试期使用；生产部署应替换为
        Stream / Span / Metric 等真实可观测性后端）。

    线程安全：非线程安全（Hook 内部同步调用足够）。
    内存：无上限；生产替换前应避免长跑。
    """

    def __init__(self) -> None:
        self._events: list[MemoryAuditEvent] = []

    @property
    def events(self) -> list[MemoryAuditEvent]:
        """已记录的 events（只读快照）。"""
        return list(self._events)

    def record(self, event: MemoryAuditEvent) -> None:
        self._events.append(event)
        # 字段顺序固定，便于日志聚合
        logger.info(
            'MemoryAuditEvent: triggered=%s reason=%s proposal_action=%s '
            'task_type=%s write_attempted=%s write_success=%s error_type=%s '
            'memory_write_mode=%s memory_resolution_reason=%s failure_category=%s',
            event.triggered,
            event.trigger_reason,
            event.proposal_action,
            event.task_type,
            event.write_attempted,
            event.write_success,
            event.error_type,
            event.memory_write_mode,
            event.memory_resolution_reason,
            event.failure_category,
        )

    def clear(self) -> None:
        """测试 / 调试用：清空累计 events。"""
        self._events.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def error_type_name(error: BaseException | None) -> str | None:
    """从异常推导 audit event 的 error_type 字符串。

    规则：
      - error is None → None（成功路径）；
      - 否则返回异常类名（不带 module 前缀，避免泄漏内部实现细节）。

    不抛错；调用方对结果做字段填充。
    """
    if error is None:
        return None
    return type(error).__name__


def safe_proposal_action(proposal: Any) -> str | None:
    """从 MemoryProposal 安全提取 action 字段。仅当对象有 .action 属性时返回。

    兼容 Pipeline 失败 / proposal=None / 任意非法对象（fail-safe）。
    """
    if proposal is None:
        return None
    action = getattr(proposal, 'action', None)
    if not isinstance(action, str):
        return None
    return action


def safe_task_type(proposal: Any) -> str | None:
    """从 MemoryProposal 安全提取 task_type 字段。规则同 safe_proposal_action。"""
    if proposal is None:
        return None
    task_type = getattr(proposal, 'task_type', None)
    if not isinstance(task_type, str):
        return None
    return task_type


def classify_failure_category(error: BaseException | None) -> FailureCategoryLiteral | None:
    """将异常映射到 FailureCategory 字面量（用于 audit 聚合告警）。

    映射规则：
      - MemoryPipelineError / Pipeline 调度类异常 → 'pipeline_error'
      - MemoryWriteDispatcherError / JavaMemoryClientError → 'dispatcher_error'
      - MemoryExtractionParseError → 'extractor_parse_error'
      - 包含 forbidden keys / task_state 超限等 → 'invalid_snapshot'
      - 其他 → 'unknown'
      - None → None
    """
    if error is None:
        return None
    type_name = type(error).__name__
    if type_name == 'MemoryPipelineError':
        return 'pipeline_error'
    if type_name in ('MemoryWriteDispatcherError', 'JavaMemoryClientError'):
        return 'dispatcher_error'
    if type_name == 'MemoryExtractionParseError':
        return 'extractor_parse_error'
    if type_name == 'ValueError' and '禁止键' in str(error):
        return 'invalid_snapshot'
    return 'unknown'


__all__ = [
    'FailureCategoryLiteral',
    'LoggingAuditRecorder',
    'MemoryAuditEvent',
    'MemoryAuditRecorder',
    'MemoryWriteModeLiteral',
    'classify_failure_category',
    'error_type_name',
    'safe_proposal_action',
    'safe_task_type',
]
