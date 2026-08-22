"""test_memory_runtime_hook.py —— MemoryRuntimeHook 集成测试（Phase 4C）

覆盖：
  - TestConstruction：默认 pipeline + 默认 dispatcher / 注入 pipeline / 注入 dispatcher。
  - TestTriggerFalse：Pipeline 返回 triggered=False → 不调 Dispatcher / written=False / error=None。
  - TestTriggeredButNoCommand：triggered=True + command=None → 不调 Dispatcher / written=False。
  - TestTriggeredWithCommand：Pipeline 返回 command → Dispatcher 被调用 / written=True / error=None。
  - TestConversationIdPassthrough：conversation_id 原样透传给 Dispatcher（通过 mock 验证）。
  - TestAgentResultNotMutated：Hook 不修改 agent_result。
  - TestDispatcherFailureCaptured：Dispatcher 抛 MemoryWriteDispatcherError → result.error 捕获 /
    written=False / triggered=True。
  - TestPipelineFailureCaptured：Pipeline 抛 MemoryPipelineError → result.error 捕获 /
    triggered=False / written=False / pipeline_result=None。
  - TestPipelineUnexpectedExceptionCaptured：Pipeline 抛 RuntimeError → result.error 仍捕获。
  - TestDispatcherUnexpectedExceptionCaptured：Dispatcher 抛 RuntimeError → result.error 仍捕获。
  - TestMemoryFailureNeverPropagates：Hook 在所有失败路径都不抛错。
  - TestResultExtraForbid：MemoryRuntimeResult 拒额外字段。
  - TestPipelineResultPassthrough：MemoryPipelineResult 完整透传。
  - TestEndToEndIntegration：Pipeline + Dispatcher + 真实 writer 串通。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.memory.memory_pipeline import (
    MemoryPipeline,
    MemoryPipelineError,
    MemoryPipelineResult,
)
from app.memory.memory_runtime_hook import (
    TERMINAL_COMMAND_BLOCKED,
    MemoryRuntimeHook,
    MemoryRuntimeResult,
)
from app.memory.memory_audit import LoggingAuditRecorder
from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_mode import make_execution_policy
from app.memory.memory_write_policy import MemoryWriteCommand
from app.schemas.memory_schema import MemoryProposal


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


CONV_ID = '11111111-1111-1111-1111-111111111111'


def _make_agent_result(
    *,
    action_proposal: dict[str, Any] | None = None,
    tool_history: list[dict[str, Any]] | None = None,
    memory_context: dict[str, Any] | None = None,
    question: str = '申请 2026-09-01 年假',
) -> dict[str, Any]:
    """agent_result 形状：保留业务语义、便于断言 not mutated。"""
    return {
        'question': question,
        'answer': '好的，已为您开始年假申请。',
        'route': 'leave_request',
        'safe': True,
        'category': 'leave',
        'tool_history': tool_history or [],
        'action_proposal': action_proposal,
        'memory_context': memory_context,
    }


def _make_command(
    *,
    action: str = 'UPSERT',
    task_type: str = 'LEAVE_REQUEST',
    status: str = 'ACTIVE',
) -> MemoryWriteCommand:
    return MemoryWriteCommand(
        action=action,
        task_type=task_type,
        status=status,
        task_state={'phase': 'clarify'},
        summary='申请 2026-09-01 年假',
    )


def _make_proposal(
    *,
    action: str = 'UPSERT',
    task_type: str = 'LEAVE_REQUEST',
    status: str = 'ACTIVE',
) -> MemoryProposal:
    return MemoryProposal(
        action=action,
        task_type=task_type,
        status=status,
        task_state={'phase': 'clarify'},
        summary='申请 2026-09-01 年假',
    )


def _make_pipeline(
    *,
    triggered: bool = True,
    command: MemoryWriteCommand | None = None,
    proposal: MemoryProposal | None = None,
    trigger_reason: str = 'action_proposal',
    raise_exception: BaseException | None = None,
) -> MemoryPipeline:
    """构造 mock MemoryPipeline：避免 trigger / extractor / write_policy 真实计算。"""
    result = MemoryPipelineResult(
        triggered=triggered,
        proposal=proposal,
        command=command,
        trigger_reason=trigger_reason,
    )

    class MockPipeline(MemoryPipeline):
        def __init__(self) -> None:
            self.processed: list[dict[str, Any]] = []
            self._raise = raise_exception
            self._result = result

        def process(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
            self.processed.append(agent_result)
            if self._raise is not None:
                raise self._raise
            return self._result

    return MockPipeline()


def _make_dispatcher(
    *,
    raise_on_dispatch: BaseException | None = None,
    return_value: Any = None,
) -> MemoryWriteDispatcher:
    """构造 mock Dispatcher（直接子类覆盖 dispatch，便于验证调用与异常）。"""
    received: list[MemoryWriteCommand] = []

    class MockDispatcher(MemoryWriteDispatcher):
        def __init__(self) -> None:
            self.received = received
            self._raise = raise_on_dispatch
            self._return = return_value

        def dispatch(self, command: MemoryWriteCommand) -> Any:
            self.received.append(command)
            if self._raise is not None:
                raise self._raise
            return self._return

    return MockDispatcher()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_construction(self) -> None:
        hook = MemoryRuntimeHook()
        assert isinstance(hook.pipeline, MemoryPipeline)
        assert isinstance(hook.dispatcher, MemoryWriteDispatcher)

    def test_inject_pipeline(self) -> None:
        custom_pipeline = MemoryPipeline()
        hook = MemoryRuntimeHook(pipeline=custom_pipeline)
        assert hook.pipeline is custom_pipeline

    def test_inject_dispatcher(self) -> None:
        custom_dispatcher = MemoryWriteDispatcher()
        hook = MemoryRuntimeHook(dispatcher=custom_dispatcher)
        assert hook.dispatcher is custom_dispatcher

    def test_inject_both(self) -> None:
        pipeline = MemoryPipeline()
        dispatcher = MemoryWriteDispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)
        assert hook.pipeline is pipeline
        assert hook.dispatcher is dispatcher


# ---------------------------------------------------------------------------
# Trigger false
# ---------------------------------------------------------------------------


class TestTriggerFalse:
    def test_trigger_false_does_not_call_dispatcher(self) -> None:
        pipeline = _make_pipeline(triggered=False, trigger_reason='no_trigger')
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert result.written is False
        assert result.error is None
        assert result.pipeline_result is not None
        assert result.pipeline_result.triggered is False
        assert dispatcher.received == []
        pipeline = _make_pipeline(triggered=False, trigger_reason='safe_short_circuit')
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.pipeline_result is not None
        assert result.pipeline_result.trigger_reason == 'safe_short_circuit'


# ---------------------------------------------------------------------------
# Triggered but no command
# ---------------------------------------------------------------------------


class TestTriggeredButNoCommand:
    def test_triggered_no_command_does_not_dispatch(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=None, proposal=None)
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert result.error is None
        assert dispatcher.received == []

    def test_triggered_no_command_with_proposal_none(self) -> None:
        # proposal.action=NONE → WritePolicy 返回 None → 不写
        pipeline = _make_pipeline(
            triggered=True,
            command=None,
            proposal=_make_proposal(action='NONE'),
        )
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert dispatcher.received == []


# ---------------------------------------------------------------------------
# Triggered with command
# ---------------------------------------------------------------------------


class TestTriggeredWithCommand:
    def test_dispatcher_called_with_command(self) -> None:
        command = _make_command()
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is True
        assert result.error is None
        assert dispatcher.received == [command]

    def test_complete_action_dispatched(self) -> None:
        command = _make_command(action='COMPLETE', status='COMPLETED')
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is True
        assert dispatcher.received == [command]

    def test_dispatcher_return_value_ignored(self) -> None:
        command = _make_command()
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher(return_value={'jti': 'abc-123'})
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        # Hook 不读 dispatcher 返回值；只关心是否成功
        assert result.written is True
        assert result.error is None


# ---------------------------------------------------------------------------
# conversation_id passthrough
# ---------------------------------------------------------------------------


class TestConversationIdPassthrough:
    def test_conversation_id_not_injected_into_command(self) -> None:
        # Hook 自身不把 conversation_id 注入 command；conversation_id 由 Dispatcher /
        # writer 内部决定是否需要（JavaMemoryClient 不接收 conversationId）。
        command = _make_command()
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        # dispatcher 收到的 command 与 Hook 收到的 command 一致；Hook 不修改
        assert dispatcher.received == [command]
        # command 序列化后不含 conversationId（command 根本没有该字段）
        import json
        serialized = json.dumps(command.model_dump())
        assert 'conversation_id' not in serialized
        assert 'conversationId' not in serialized

    def test_various_conversation_ids_accepted(self) -> None:
        for conv_id in [
            '11111111-1111-1111-1111-111111111111',
            'short-id',
            'foo.bar:baz',
            'a' * 64,
        ]:
            pipeline = _make_pipeline(triggered=False)
            dispatcher = _make_dispatcher()
            hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

            # 不抛错即可
            result = hook.after_agent_response(_make_agent_result(), conv_id)
            assert result.triggered is False


# ---------------------------------------------------------------------------
# agent_result not mutated
# ---------------------------------------------------------------------------


class TestAgentResultNotMutated:
    def test_agent_result_dict_not_modified(self) -> None:
        original = _make_agent_result()
        snapshot = dict(original)
        snapshot['tool_history'] = list(original['tool_history'])
        if original['action_proposal'] is not None:
            snapshot['action_proposal'] = dict(original['action_proposal'])

        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        hook.after_agent_response(original, CONV_ID)

        # 浅比较（dict 顶层键 + 顶层值）
        assert original == snapshot

    def test_agent_result_not_mutated_on_failure(self) -> None:
        original = _make_agent_result()
        snapshot = dict(original)

        pipeline = _make_pipeline(raise_exception=MemoryPipelineError('boom'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        hook.after_agent_response(original, CONV_ID)

        assert original == snapshot

    def test_agent_result_not_mutated_on_dispatcher_failure(self) -> None:
        original = _make_agent_result()
        snapshot = dict(original)

        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(raise_on_dispatch=MemoryWriteDispatcherError('boom'))
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        hook.after_agent_response(original, CONV_ID)

        assert original == snapshot


# ---------------------------------------------------------------------------
# Dispatcher failure
# ---------------------------------------------------------------------------


class TestDispatcherFailure:
    def test_dispatcher_error_captured(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(
            raise_on_dispatch=MemoryWriteDispatcherError('java down'),
        )
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert isinstance(result.error, MemoryWriteDispatcherError)
        assert 'java down' in str(result.error)

    def test_dispatcher_unexpected_exception_captured(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(raise_on_dispatch=RuntimeError('unexpected'))
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert isinstance(result.error, MemoryWriteDispatcherError)
        # __cause__ 保留原始异常
        assert isinstance(result.error.__cause__, RuntimeError)

    def test_dispatcher_error_pipeline_result_preserved(self) -> None:
        pipeline_result = MemoryPipelineResult(
            triggered=True,
            proposal=_make_proposal(),
            command=_make_command(),
            trigger_reason='action_proposal',
        )
        pipeline = MagicMock(spec=MemoryPipeline)
        pipeline.process.return_value = pipeline_result
        dispatcher = _make_dispatcher(
            raise_on_dispatch=MemoryWriteDispatcherError('boom'),
        )
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.pipeline_result is pipeline_result


# ---------------------------------------------------------------------------
# Pipeline failure
# ---------------------------------------------------------------------------


class TestPipelineFailure:
    def test_pipeline_error_captured(self) -> None:
        pipeline = _make_pipeline(raise_exception=MemoryPipelineError('pipeline boom'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert result.written is False
        assert isinstance(result.error, MemoryPipelineError)
        assert result.pipeline_result is None
        assert dispatcher.received == []

    def test_pipeline_unexpected_exception_wrapped(self) -> None:
        pipeline = _make_pipeline(raise_exception=RuntimeError('crash'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert result.written is False
        assert isinstance(result.error, MemoryPipelineError)
        assert isinstance(result.error.__cause__, RuntimeError)
        assert dispatcher.received == []

    def test_pipeline_value_error_wrapped(self) -> None:
        pipeline = _make_pipeline(raise_exception=ValueError('bad input'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert isinstance(result.error, MemoryPipelineError)
        assert isinstance(result.error.__cause__, ValueError)


# ---------------------------------------------------------------------------
# Memory failure never propagates
# ---------------------------------------------------------------------------


class TestMemoryFailureNeverPropagates:
    def test_pipeline_failure_does_not_raise(self) -> None:
        pipeline = _make_pipeline(raise_exception=MemoryPipelineError('boom'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        # 不抛错
        result = hook.after_agent_response(_make_agent_result(), CONV_ID)
        assert result.error is not None

    def test_dispatcher_failure_does_not_raise(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(
            raise_on_dispatch=MemoryWriteDispatcherError('boom'),
        )
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)
        assert result.error is not None

    def test_pipeline_unexpected_exception_does_not_raise(self) -> None:
        pipeline = _make_pipeline(raise_exception=KeyError('missing'))
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        # 不抛错
        result = hook.after_agent_response(_make_agent_result(), CONV_ID)
        assert result.error is not None

    def test_dispatcher_unexpected_exception_does_not_raise(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(raise_on_dispatch=OSError('network'))
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        # 不抛错
        result = hook.after_agent_response(_make_agent_result(), CONV_ID)
        assert result.error is not None


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


class TestInputContract:
    def test_reject_none_agent_result(self) -> None:
        hook = MemoryRuntimeHook()
        with pytest.raises(TypeError):
            hook.after_agent_response(None, CONV_ID)  # type: ignore[arg-type]

    def test_reject_string_agent_result(self) -> None:
        hook = MemoryRuntimeHook()
        with pytest.raises(TypeError):
            hook.after_agent_response('not a dict', CONV_ID)  # type: ignore[arg-type]

    def test_reject_int_agent_result(self) -> None:
        hook = MemoryRuntimeHook()
        with pytest.raises(TypeError):
            hook.after_agent_response(42, CONV_ID)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MemoryRuntimeResult schema
# ---------------------------------------------------------------------------


class TestMemoryRuntimeResultSchema:
    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRuntimeResult(
                triggered=True,
                written=True,
                pipeline_result=None,
                error=None,
                extra_field='not allowed',  # type: ignore[call-arg]
            )

    def test_required_fields(self) -> None:
        # triggered / written 必填
        with pytest.raises(ValidationError):
            MemoryRuntimeResult()  # type: ignore[call-arg]

    def test_default_optional_fields(self) -> None:
        result = MemoryRuntimeResult(triggered=False, written=False)
        assert result.pipeline_result is None
        assert result.error is None

    def test_error_is_base_exception(self) -> None:
        # error 字段类型是 BaseException（宽松；Pipeline / Dispatcher 错误都接受）
        result = MemoryRuntimeResult(
            triggered=False,
            written=False,
            error=MemoryPipelineError('boom'),
        )
        assert isinstance(result.error, BaseException)


# ---------------------------------------------------------------------------
# Pipeline result passthrough
# ---------------------------------------------------------------------------


class TestPipelineResultPassthrough:
    def test_pipeline_result_full_passthrough(self) -> None:
        full_result = MemoryPipelineResult(
            triggered=True,
            proposal=_make_proposal(),
            command=_make_command(),
            trigger_reason='action_proposal',
        )
        pipeline = MagicMock(spec=MemoryPipeline)
        pipeline.process.return_value = full_result
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.pipeline_result is full_result

    def test_pipeline_result_with_error_field_passthrough(self) -> None:
        # Pipeline 返回 error 字段（理论上 Pipeline 已抛错，但兼容性测试）
        err = MemoryPipelineError('internal')
        full_result = MemoryPipelineResult(
            triggered=False,
            trigger_reason='pipeline_error',
            error=err,
        )
        pipeline = MagicMock(spec=MemoryPipeline)
        pipeline.process.return_value = full_result
        dispatcher = _make_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.pipeline_result is full_result
        assert result.pipeline_result.error is err


# ---------------------------------------------------------------------------
# 业务动作链路终态命令拦截（terminal_command_blocked）
# ---------------------------------------------------------------------------


class TestBusinessActionTerminalBlocked:
    """业务动作链路（action_proposal 非空 / leave_proposal_tool 成功）下，
    COMPLETE / ABANDON 终态命令必须被程序层拦截：
      - Dispatcher 不被调用（不写 Java）；
      - written=False；
      - audit 记录 error_type=terminal_command_blocked；
      - 主响应不被阻断（error=None）。
    终态只能由 Java PendingAction 生命周期收口。
    """

    def _hook_with_audit(self, pipeline, dispatcher):
        audit = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline,
            dispatcher=dispatcher,
            audit_recorder=audit,
            write_execution_policy=make_execution_policy('ENABLED'),
        )
        return hook, audit

    def test_business_action_complete_blocked(self):
        pipeline = _make_pipeline(
            triggered=True,
            command=_make_command(action='COMPLETE', status='COMPLETED'),
            proposal=_make_proposal(action='COMPLETE', status='COMPLETED'),
        )
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(action_proposal={'action_type': 'ANNUAL_LEAVE_REQUEST'}),
            CONV_ID,
        )

        assert result.triggered is True
        assert result.written is False
        assert result.error is None  # 策略拦截不是错误，主响应不阻断
        assert dispatcher.received == []  # Dispatcher 不被调用
        assert audit.events[-1].error_type == TERMINAL_COMMAND_BLOCKED
        assert audit.events[-1].write_attempted is False

    def test_business_action_abandon_blocked(self):
        pipeline = _make_pipeline(
            triggered=True,
            command=_make_command(action='ABANDON', status='ABANDONED'),
            proposal=_make_proposal(action='ABANDON', status='ABANDONED'),
        )
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(action_proposal={'action_type': 'ANNUAL_LEAVE_REQUEST'}),
            CONV_ID,
        )

        assert result.written is False
        assert result.error is None
        assert dispatcher.received == []
        assert audit.events[-1].error_type == TERMINAL_COMMAND_BLOCKED
        assert audit.events[-1].write_attempted is False

    def test_business_action_tool_success_path_blocked(self):
        """tool_history 中 leave_proposal_tool 成功（Clarification 场景，
        action_proposal 可能为空）同样视为业务动作链路。"""
        pipeline = _make_pipeline(
            triggered=True,
            command=_make_command(action='COMPLETE', status='COMPLETED'),
            proposal=_make_proposal(action='COMPLETE', status='COMPLETED'),
        )
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(
                tool_history=[
                    {'tool_name': 'leave_proposal_tool', 'status': 'success',
                     'arguments': {}, 'observation': 'ok'},
                ],
            ),
            CONV_ID,
        )

        assert result.written is False
        assert dispatcher.received == []
        assert audit.events[-1].error_type == TERMINAL_COMMAND_BLOCKED

    def test_business_action_upsert_still_dispatched(self):
        """业务动作链路 + UPSERT + ACTIVE：上下文更新正常 dispatch。"""
        command = _make_command()
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(action_proposal={'action_type': 'ANNUAL_LEAVE_REQUEST'}),
            CONV_ID,
        )

        assert result.written is True
        assert result.error is None
        assert dispatcher.received == [command]
        assert audit.events[-1].error_type is None  # 非拦截、非失败

    def test_plain_flow_complete_keeps_existing_behavior(self):
        """非业务动作链路（无 action_proposal / 无 eligible tool）保持既有行为：
        COMPLETE 正常 dispatch（Python 可终结普通任务的 Memory）。"""
        command = _make_command(action='COMPLETE', status='COMPLETED')
        pipeline = _make_pipeline(triggered=True, command=command)
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(action_proposal=None),
            CONV_ID,
        )

        assert result.written is True
        assert dispatcher.received == [command]
        assert audit.events[-1].error_type is None

    def test_pipeline_level_blocked_command_also_audited(self):
        """Pipeline 层已拦截（command=None + 终态 proposal）时，Hook 同样补记
        terminal_command_blocked，两条拦截路径审计语义一致。"""
        pipeline = _make_pipeline(
            triggered=True,
            command=None,
            proposal=_make_proposal(action='COMPLETE', status='COMPLETED'),
        )
        dispatcher = _make_dispatcher()
        hook, audit = self._hook_with_audit(pipeline, dispatcher)

        result = hook.after_agent_response(
            _make_agent_result(action_proposal={'action_type': 'ANNUAL_LEAVE_REQUEST'}),
            CONV_ID,
        )

        assert result.triggered is True
        assert result.written is False
        assert result.error is None
        assert dispatcher.received == []
        assert audit.events[-1].error_type == TERMINAL_COMMAND_BLOCKED


# ---------------------------------------------------------------------------
# End-to-end integration
# ---------------------------------------------------------------------------


class TestEndToEndIntegration:
    def test_real_pipeline_with_real_dispatcher_no_writer(self) -> None:
        # 真实组件 + 默认 dispatcher（无 writer 时不调）
        pipeline = MemoryPipeline()
        dispatcher = MemoryWriteDispatcher()  # 无 writer
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        # 构造能触发 Pipeline 的 agent_result
        agent_result = _make_agent_result(
            action_proposal={
                'action_type': 'leave_request',
                'employee_id': 'E001',
                'start_date': '2026-09-01',
                'end_date': '2026-09-03',
                'leave_type': 'annual',
                'reason': '休息',
            },
        )

        # 不抛错即可（Pipeline 会因 NotImplementedError / Command None 走 fail-safe）
        result = hook.after_agent_response(agent_result, CONV_ID)
        assert result is not None

    def test_real_pipeline_with_function_writer_dispatcher(self) -> None:
        # 真实 Pipeline + 函数 writer dispatcher（模拟 Java 写入）
        pipeline = MemoryPipeline()
        received: list[MemoryWriteCommand] = []
        dispatcher = MemoryWriteDispatcher(writer=lambda cmd: received.append(cmd))
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        agent_result = _make_agent_result(
            action_proposal={
                'action_type': 'leave_request',
                'employee_id': 'E001',
                'start_date': '2026-09-01',
                'end_date': '2026-09-03',
                'leave_type': 'annual',
                'reason': '休息',
            },
        )

        # Pipeline 默认无 LLM → Extractor 抛 NotImplementedError → 降级为 triggered=True + proposal=None
        # 因此不会调 Dispatcher
        result = hook.after_agent_response(agent_result, CONV_ID)
        assert result.triggered is True
        assert result.written is False  # 因 pipeline 没产出 command
        assert received == []  # dispatcher 未被调用

    def test_complete_chain_trigger_to_dispatch(self) -> None:
        """完整链：Pipeline (mock) → Dispatcher → function writer 模拟 Java Client。"""
        command = _make_command()
        pipeline = _make_pipeline(triggered=True, command=command)
        received: list[MemoryWriteCommand] = []

        def java_client_writer(cmd: MemoryWriteCommand) -> dict[str, Any]:
            received.append(cmd)
            return {'jti': 'persisted-123'}

        dispatcher = MemoryWriteDispatcher(writer=java_client_writer)
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is True
        assert result.error is None
        assert received == [command]
