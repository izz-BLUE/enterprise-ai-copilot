"""memory_runtime_hook.py —— Memory Runtime Hook 集成层（Phase 4C）

职责：
  把 Memory 提案链路编排进 Agent 响应生命周期：
    agent_result → MemoryPipeline → MemoryWriteCommand → Dispatcher → response writer

  Hook 是 LangGraph Agent 出口层与 Memory 写入链路之间的**业务适配边界**：

    1. 接收 run_langgraph_agent 输出的 agent_result dict；
    2. 调用 MemoryPipeline.process 产出 MemoryPipelineResult；
    3. 若触发且有 command，调 Dispatcher 写入；
    4. 任何 Pipeline / Dispatcher 异常一律落入 MemoryRuntimeResult.error，
       **绝不冒泡到 Agent 出口**——保证主响应永远不被 Memory 失败阻断。

边界与不变量：
  - conversation_id 来自 Java 端（X-Conversation-Id header），仅用于审计关联；
    Hook 不创建 conversationId、不从 memory_context 推导、不生成 userId。
  - Hook 不接 HTTP，不持有 Java 服务凭证；
  - Hook 不修改 LangGraph / AgentState / Planner / Tool Executor；
  - Hook 修改 main.py 出口层（最小改动），不改 run_langgraph_agent 本身。

Error Boundary：
  Hook 自身必须 fail-safe。任何异常（Pipeline / Dispatcher / Hook 自身 bug）
  全部落入 result.error，调用方继续走主返回路径。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.memory.memory_audit import (
    LoggingAuditRecorder,
    MemoryAuditEvent,
    MemoryAuditRecorder,
    classify_failure_category,
    error_type_name,
    safe_proposal_action,
    safe_task_type,
)
from app.memory.memory_pipeline import (
    MemoryPipeline,
    MemoryPipelineError,
    MemoryPipelineResult,
)
from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_mode import (
    MemoryWriteExecutionPolicy,
    make_execution_policy,
)
from app.memory.memory_write_policy import MemoryWriteCommand

logger = logging.getLogger(__name__)

# Python 终态命令被程序拦截的 audit error_type 标记。
# 这不是异常，而是确定性策略拦截：终态只能由 Java PendingAction 生命周期收口。
TERMINAL_COMMAND_BLOCKED = 'terminal_command_blocked'


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class MemoryRuntimeResult(BaseModel):
    """Memory Runtime Hook 单次执行的最终输出。

    字段语义：
      triggered        —— Pipeline 是否判定"值得调用"。
      written          —— Dispatcher 是否实际被调用并成功返回（写入层成功）。
      pipeline_result  —— 原始 Pipeline 输出（triggered / proposal / command / trigger_reason / error）。
      error            —— Hook 层的失败信号（Pipeline / Dispatcher / 自身异常包装）；
                          成功路径恒为 None。

    失败永远由调用方读取 result.error 做下游策略；Hook.process 永不抛错。
    """

    model_config = ConfigDict(extra='forbid', arbitrary_types_allowed=True)

    triggered: bool
    written: bool
    pipeline_result: MemoryPipelineResult | None = None
    error: BaseException | None = None


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


class MemoryRuntimeHook:
    """Memory Runtime Hook —— Agent 出口层 + Memory Write 链路的编排层。

    用法（main.py 出口层最小改动）：
        hook = MemoryRuntimeHook(
            pipeline=MemoryPipeline(),
            dispatcher=MemoryWriteDispatcher(writer=response_writer),
        )
        result = hook.after_agent_response(agent_result, conversation_id)
        # result 永不抛错；不修改 agent_result；不阻断主响应。

    构造约束：
      - pipeline / dispatcher 都接受 None（默认占位实现）：
        - pipeline   默认 MemoryPipeline()
        - dispatcher 默认 MemoryWriteDispatcher()（无 writer，no-op）
      - Hook 自身必须能在 P0 阶段（无 Java Client / 无 LLM）完整跑通。
    """

    def __init__(
        self,
        pipeline: MemoryPipeline | None = None,
        dispatcher: MemoryWriteDispatcher | None = None,
        audit_recorder: MemoryAuditRecorder | None = None,
        write_execution_policy: MemoryWriteExecutionPolicy | None = None,
    ) -> None:
        self._pipeline = pipeline or MemoryPipeline()
        self._dispatcher = dispatcher or MemoryWriteDispatcher()
        self._audit_recorder = audit_recorder or LoggingAuditRecorder()
        # 默认 DISABLED：与 P0 阶段一致（实际不写入，仅 Pipeline fail-safe noop）。
        self._write_execution_policy = write_execution_policy or make_execution_policy('DISABLED')

    @property
    def pipeline(self) -> MemoryPipeline:
        return self._pipeline

    @property
    def dispatcher(self) -> MemoryWriteDispatcher:
        return self._dispatcher

    @property
    def audit_recorder(self) -> MemoryAuditRecorder:
        return self._audit_recorder

    @property
    def write_execution_policy(self) -> MemoryWriteExecutionPolicy:
        return self._write_execution_policy

    def _is_terminal_command(self, command: MemoryWriteCommand) -> bool:
        return (
            command.action in ('COMPLETE', 'ABANDON')
            or command.status in ('COMPLETED', 'ABANDONED')
        )

    def after_agent_response(
        self,
        agent_result: dict[str, Any],
        conversation_id: str,
    ) -> MemoryRuntimeResult:
        """Agent 响应完成后调用，把 Memory Write 链路串完。

        行为：
          1. 调用 Pipeline.process(agent_result) → MemoryPipelineResult；
          2. Pipeline 抛错 → 落入 result.error（triggered=False, written=False）；
          3. triggered=False → 直接返回（不调 Dispatcher）；
          4. triggered=True 但 command is None → 直接返回（NONE / WritePolicy 拒绝）；
          5. triggered=True 且 command 存在 → Dispatcher.dispatch(command)；
          6. Dispatcher 抛错 → 落入 result.error（written=False, triggered=True）。
          7. 最后调用 audit_recorder.record(event)；recorder 失败仅记日志。

        抛出：
          TypeError —— agent_result 不是 dict（防御性，Hook 不主动校验为失败）。

        不修改 agent_result（只读取）。
        """
        if not isinstance(agent_result, dict):
            raise TypeError(
                f'MemoryRuntimeHook.after_agent_response 需要 dict 输入，'
                f'得到 {type(agent_result).__name__}'
            )

        # 1. Pipeline
        try:
            pipeline_result = self._pipeline.process(agent_result)
        except MemoryPipelineError as exc:
            logger.warning(
                'MemoryRuntimeHook: Pipeline 调度失败 (conversation_id=%s): %s',
                conversation_id, exc,
            )
            result = MemoryRuntimeResult(
                triggered=False,
                written=False,
                pipeline_result=None,
                error=exc,
            )
            self._emit_audit(
                triggered=False,
                trigger_reason='pipeline_error',
                proposal=None,
                write_attempted=False,
                write_success=False,
                error=exc,
            )
            return result
        except Exception as exc:  # noqa: BLE001 —— Hook 自身必须 fail-safe
            # 防御：Pipeline 未声明的"非预期"异常。
            wrapped = MemoryPipelineError(
                f'MemoryRuntimeHook Pipeline 兜底异常: {type(exc).__name__}: {exc}'
            )
            wrapped.__cause__ = exc
            logger.warning(
                'MemoryRuntimeHook: Pipeline 意外异常 (conversation_id=%s): %s',
                conversation_id, exc,
            )
            result = MemoryRuntimeResult(
                triggered=False,
                written=False,
                pipeline_result=None,
                error=wrapped,
            )
            self._emit_audit(
                triggered=False,
                trigger_reason='pipeline_error',
                proposal=None,
                write_attempted=False,
                write_success=False,
                error=wrapped,
            )
            return result

        # 2. Triggered=False → 不写
        if not pipeline_result.triggered:
            logger.debug(
                'MemoryRuntimeHook: 未触发 (conversation_id=%s reason=%s)',
                conversation_id, pipeline_result.trigger_reason,
            )
            self._emit_audit(
                triggered=False,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                write_attempted=False,
                write_success=False,
                error=None,
            )
            return MemoryRuntimeResult(
                triggered=False,
                written=False,
                pipeline_result=pipeline_result,
                error=None,
            )

        # 3. Triggered=True 但 command 为 None → NONE / WritePolicy 拒绝
        if pipeline_result.command is None:
            # 终态 proposal 已由 WritePolicy 在 Pipeline 层拦截。
            terminal_blocked = (
                pipeline_result.proposal is not None
                and (
                    pipeline_result.proposal.action in ('COMPLETE', 'ABANDON')
                    or pipeline_result.proposal.status in ('COMPLETED', 'ABANDONED')
                )
            )
            logger.info(
                'MemoryRuntimeHook: 触发但无 command (action=NONE 或 WritePolicy 拒绝'
                '%s, conversation_id=%s)',
                '，终态被拦截' if terminal_blocked else '',
                conversation_id,
            )
            self._emit_audit(
                triggered=True,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                write_attempted=False,
                write_success=False,
                error=None,
                terminal_blocked=terminal_blocked,
            )
            return MemoryRuntimeResult(
                triggered=True,
                written=False,
                pipeline_result=pipeline_result,
                error=None,
            )

        # 4. 终态命令兜底拦截：即使注入的自定义 Pipeline 绕过 WritePolicy，
        #    Python 也不能把终态提案交给 Java。
        if self._is_terminal_command(pipeline_result.command):
            logger.warning(
                'MemoryRuntimeHook: Python 终态命令被拦截 '
                '(conversation_id=%s, action=%s, task_type=%s)',
                conversation_id, pipeline_result.command.action,
                pipeline_result.command.task_type,
            )
            self._emit_audit(
                triggered=True,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                write_attempted=False,
                write_success=False,
                error=None,
                terminal_blocked=True,
            )
            return MemoryRuntimeResult(
                triggered=True,
                written=False,
                pipeline_result=pipeline_result,
                error=None,
            )

        # 5. Execution Mode 决策（Phase 4E）：DISABLED / AUDIT_ONLY 不调 Dispatcher
        command = pipeline_result.command
        if not self._write_execution_policy.should_dispatch(command):
            mode = self._write_execution_policy.mode_value()
            logger.info(
                'MemoryRuntimeHook: 模式=%s 跳过 dispatch (conversation_id=%s, action=%s, task_type=%s)',
                mode, conversation_id, command.action, command.task_type,
            )
            self._emit_audit(
                triggered=True,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                # DISABLED / AUDIT_ONLY 都视为"未尝试写入"；
                # AuditOnly 模式语义上"如果开了就会写"，但本实例未真正写入。
                write_attempted=False,
                write_success=False,
                # error_type 留 None：这不是错误，是策略选择。
                error=None,
            )
            return MemoryRuntimeResult(
                triggered=True,
                written=False,
                pipeline_result=pipeline_result,
                error=None,
            )

        # 5. Dispatcher
        logger.info(
            'MemoryRuntimeHook: Dispatching memory write (conversation_id=%s, action=%s, task_type=%s)',
            conversation_id, command.action, command.task_type,
        )

        try:
            self._dispatcher(command)
        except MemoryWriteDispatcherError as exc:
            logger.warning(
                'MemoryRuntimeHook: Dispatcher 失败 (conversation_id=%s): %s',
                conversation_id, exc,
            )
            self._emit_audit(
                triggered=True,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                write_attempted=True,
                write_success=False,
                error=exc,
            )
            return MemoryRuntimeResult(
                triggered=True,
                written=False,
                pipeline_result=pipeline_result,
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001 —— Hook 自身必须 fail-safe
            wrapped = MemoryWriteDispatcherError(
                f'MemoryRuntimeHook Dispatcher 兜底异常: {type(exc).__name__}: {exc}'
            )
            wrapped.__cause__ = exc
            logger.warning(
                'MemoryRuntimeHook: Dispatcher 意外异常 (conversation_id=%s): %s',
                conversation_id, exc,
            )
            self._emit_audit(
                triggered=True,
                trigger_reason=pipeline_result.trigger_reason,
                proposal=pipeline_result.proposal,
                write_attempted=True,
                write_success=False,
                error=wrapped,
            )
            return MemoryRuntimeResult(
                triggered=True,
                written=False,
                pipeline_result=pipeline_result,
                error=wrapped,
            )

        # 5. 成功
        self._emit_audit(
            triggered=True,
            trigger_reason=pipeline_result.trigger_reason,
            proposal=pipeline_result.proposal,
            write_attempted=True,
            write_success=True,
            error=None,
        )
        return MemoryRuntimeResult(
            triggered=True,
            written=True,
            pipeline_result=pipeline_result,
            error=None,
        )

    def _emit_audit(
        self,
        *,
        triggered: bool,
        trigger_reason: str,
        proposal: Any,
        write_attempted: bool,
        write_success: bool,
        error: BaseException | None,
        terminal_blocked: bool = False,
    ) -> None:
        """构造审计事件并交给 recorder；recorder 失败仅记日志。

        Phase 8-A Observability：
          - 注入 ``memory_write_mode``（当前 Rollout Mode）；
          - 注入 ``failure_category``（按异常类别聚合）；
          - ``memory_resolution_reason`` 保留为空字符串（Read Path 集成后由
            MemoryTaskResolutionPolicy 注入；当前 Hook 范围仅 Write Path）。

        terminal_blocked：Python 终态命令被程序拦截（非异常）——
        error_type 记为 ``terminal_command_blocked``，failure_category 为 None。
        """
        event = MemoryAuditEvent(
            triggered=triggered,
            trigger_reason=trigger_reason,
            proposal_action=safe_proposal_action(proposal),
            task_type=safe_task_type(proposal),
            write_attempted=write_attempted,
            write_success=write_success,
            error_type=(
                TERMINAL_COMMAND_BLOCKED if terminal_blocked else error_type_name(error)
            ),
            memory_write_mode=self._write_execution_policy.mode_value(),
            memory_resolution_reason='',  # Read Path 集成后填入
            failure_category=None if terminal_blocked else classify_failure_category(error),
        )
        try:
            self._audit_recorder.record(event)
        except Exception as exc:  # noqa: BLE001 —— Audit 失败绝不阻断
            logger.warning(
                'MemoryRuntimeHook: AuditRecorder 失败 (event=%s): %s',
                event.model_dump(exclude_none=True), exc,
            )
