"""test_memory_write_mode.py —— Memory Write Execution Mode 测试（Phase 4E）

覆盖：
  - TestPolicyConstruction：
    - DISABLED / AUDIT_ONLY / ENABLED 三种 mode 构造；
    - 默认 mode = DISABLED；
    - 非法 mode 抛 MemoryWriteModeError / ValidationError；
    - Pydantic 构造时非法 mode 同样拒绝。
  - TestShouldDispatch：
    - DISABLED + command → False；
    - AUDIT_ONLY + command → False；
    - ENABLED + command → True；
    - 三种 mode + command=None → 全部 False。
  - TestPolicyImmutability：
    - frozen=True 不可写；
    - extra='forbid' 拒额外字段。
  - TestHookIntegrationDISABLED：
    - Pipeline 返回 command → Hook 不调 Dispatcher；
    - audit event: triggered=True, write_attempted=False, write_success=False, error_type=None。
  - TestHookIntegrationAUDIT_ONLY：
    - Pipeline 返回 command → Hook 不调 Dispatcher；
    - audit event 仍产生（write_attempted=False, error_type=None。
  - TestHookIntegrationENABLED：
    - Pipeline 返回 command → Hook 调 Dispatcher；
    - audit event: write_attempted=True, write_success=True/Failure 取决于 Dispatcher。
  - TestCommandNoneNotAffectedByMode：
    - 三种 mode + command=None → Hook 不调 Dispatcher；
    - audit event 字段保持一致。
  - TestHookDefaultPolicy：
    - 默认 Hook 使用 DISABLED 模式（P0 阶段不写入）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.memory.memory_audit import LoggingAuditRecorder
from app.memory.memory_pipeline import (
    MemoryPipeline,
    MemoryPipelineResult,
)
from app.memory.memory_runtime_hook import MemoryRuntimeHook
from app.memory.memory_write_dispatcher import MemoryWriteDispatcher
from app.memory.memory_write_mode import (
    MemoryWriteExecutionPolicy,
    MemoryWriteModeError,
    make_execution_policy,
)
from app.memory.memory_write_policy import MemoryWriteCommand
from app.schemas.memory_schema import MemoryProposal

CONV_ID = '11111111-1111-1111-1111-111111111111'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_agent_result() -> dict[str, Any]:
    return {
        'question': '申请 2026-09-01 年假',
        'answer': '好的。',
        'route': 'leave_request',
        'safe': True,
        'category': 'leave',
        'tool_history': [],
        'action_proposal': {
            'action_type': 'leave_request',
            'employee_id': 'E001',
            'start_date': '2026-09-01',
            'end_date': '2026-09-03',
        },
        'memory_context': None,
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


def _make_proposal() -> MemoryProposal:
    return _make_command().model_dump() if False else MemoryProposal(  # noqa: SIM222
        action='UPSERT',
        task_type='LEAVE_REQUEST',
        status='ACTIVE',
        task_state={'phase': 'clarify'},
        summary='申请 2026-09-01 年假',
    )


def _make_command_pipeline(command: MemoryWriteCommand | None) -> MemoryPipeline:
    """Pipeline 返回 (triggered=True, command=...)：模拟"有命令"路径。"""
    result = MemoryPipelineResult(
        triggered=True,
        proposal=_make_proposal(),
        command=command,
        trigger_reason='action_proposal',
    )

    class MockPipeline(MemoryPipeline):
        def process(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
            return result

    return MockPipeline()


def _make_no_command_pipeline() -> MemoryPipeline:
    """Pipeline 返回 (triggered=True, command=None)：模拟"NONE / 拒绝"路径。"""
    result = MemoryPipelineResult(
        triggered=True,
        proposal=None,
        command=None,
        trigger_reason='action_proposal',
    )

    class MockPipeline(MemoryPipeline):
        def process(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
            return result

    return MockPipeline()


def _make_recording_dispatcher() -> MemoryWriteDispatcher:
    received: list[MemoryWriteCommand] = []

    class MockDispatcher(MemoryWriteDispatcher):
        def __init__(self) -> None:
            self.received = received

        def dispatch(self, command: MemoryWriteCommand) -> Any:
            self.received.append(command)
            return {'jti': 'persisted'}

    return MockDispatcher()


# ---------------------------------------------------------------------------
# Policy construction
# ---------------------------------------------------------------------------


class TestPolicyConstruction:
    def test_disabled_via_factory(self) -> None:
        p = make_execution_policy('DISABLED')
        assert p.mode == 'DISABLED'
        assert p.mode_value() == 'DISABLED'

    def test_audit_only_via_factory(self) -> None:
        p = make_execution_policy('AUDIT_ONLY')
        assert p.mode == 'AUDIT_ONLY'
        assert p.mode_value() == 'AUDIT_ONLY'

    def test_enabled_via_factory(self) -> None:
        p = make_execution_policy('ENABLED')
        assert p.mode == 'ENABLED'
        assert p.mode_value() == 'ENABLED'

    def test_default_mode_is_disabled(self) -> None:
        p = MemoryWriteExecutionPolicy()
        assert p.mode == 'DISABLED'

    def test_factory_rejects_lowercase(self) -> None:
        with pytest.raises(MemoryWriteModeError):
            make_execution_policy('disabled')

    def test_factory_rejects_unknown(self) -> None:
        with pytest.raises(MemoryWriteModeError):
            make_execution_policy('PROD')

    def test_factory_rejects_empty(self) -> None:
        with pytest.raises(MemoryWriteModeError):
            make_execution_policy('')

    def test_factory_rejects_none(self) -> None:
        with pytest.raises(MemoryWriteModeError):
            make_execution_policy(None)  # type: ignore[arg-type]

    def test_pydantic_rejects_invalid_mode(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteExecutionPolicy(mode='NOT_A_MODE')  # type: ignore[arg-type]

    def test_pydantic_rejects_lowercase(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteExecutionPolicy(mode='enabled')  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# should_dispatch
# ---------------------------------------------------------------------------


class TestShouldDispatch:
    def test_disabled_with_command_returns_false(self) -> None:
        p = make_execution_policy('DISABLED')
        assert p.should_dispatch(_make_command()) is False

    def test_audit_only_with_command_returns_false(self) -> None:
        p = make_execution_policy('AUDIT_ONLY')
        assert p.should_dispatch(_make_command()) is False

    def test_enabled_with_command_returns_true(self) -> None:
        p = make_execution_policy('ENABLED')
        assert p.should_dispatch(_make_command()) is True

    @pytest.mark.parametrize('mode', ['DISABLED', 'AUDIT_ONLY', 'ENABLED'])
    def test_none_command_always_returns_false(self, mode: str) -> None:
        p = make_execution_policy(mode)
        assert p.should_dispatch(None) is False


# ---------------------------------------------------------------------------
# Policy immutability
# ---------------------------------------------------------------------------


class TestPolicyImmutability:
    def test_frozen_cannot_set_mode(self) -> None:
        p = make_execution_policy('DISABLED')
        with pytest.raises(ValidationError):
            p.mode = 'ENABLED'  # type: ignore[misc]

    def test_extra_forbid_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteExecutionPolicy(mode='DISABLED', leaky_field='x')  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Hook integration: DISABLED
# ---------------------------------------------------------------------------


class TestHookIntegrationDISABLED:
    def test_disabled_does_not_dispatch(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('DISABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert dispatcher.received == []
        assert result.error is None

    def test_disabled_emits_audit_event(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('DISABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is True
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type is None


# ---------------------------------------------------------------------------
# Hook integration: AUDIT_ONLY
# ---------------------------------------------------------------------------


class TestHookIntegrationAUDIT_ONLY:
    def test_audit_only_does_not_dispatch(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('AUDIT_ONLY')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert dispatcher.received == []

    def test_audit_only_emits_event_with_no_error(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('AUDIT_ONLY')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is True
        assert event.proposal_action == 'UPSERT'
        assert event.task_type == 'LEAVE_REQUEST'
        assert event.write_attempted is False
        assert event.write_success is False
        # 关键：audit_only 不是错误，error_type 必须 None
        assert event.error_type is None


# ---------------------------------------------------------------------------
# Hook integration: ENABLED
# ---------------------------------------------------------------------------


class TestHookIntegrationENABLED:
    def test_enabled_dispatches(self) -> None:
        command = _make_command()
        pipeline = _make_command_pipeline(command)
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('ENABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is True
        assert dispatcher.received == [command]

    def test_enabled_write_success_event(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('ENABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        event = recorder.events[0]
        assert event.write_attempted is True
        assert event.write_success is True
        assert event.error_type is None

    def test_enabled_write_failure_event(self) -> None:
        from app.memory.memory_write_dispatcher import MemoryWriteDispatcherError

        command = _make_command()
        pipeline = _make_command_pipeline(command)

        class FailingDispatcher(MemoryWriteDispatcher):
            def __init__(self) -> None:
                self.received: list[MemoryWriteCommand] = []

            def dispatch(self, command: MemoryWriteCommand) -> Any:
                self.received.append(command)
                raise MemoryWriteDispatcherError('java down')

        dispatcher = FailingDispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy('ENABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is False
        event = recorder.events[0]
        assert event.write_attempted is True  # ENABLED 模式：确实尝试了
        assert event.write_success is False
        assert event.error_type == 'MemoryWriteDispatcherError'


# ---------------------------------------------------------------------------
# Command None is independent of mode
# ---------------------------------------------------------------------------


class TestCommandNoneNotAffectedByMode:
    @pytest.mark.parametrize('mode', ['DISABLED', 'AUDIT_ONLY', 'ENABLED'])
    def test_none_command_does_not_dispatch_in_any_mode(self, mode: str) -> None:
        pipeline = _make_no_command_pipeline()
        dispatcher = _make_recording_dispatcher()
        policy = make_execution_policy(mode)
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert dispatcher.received == []
        assert result.error is None

    @pytest.mark.parametrize('mode', ['DISABLED', 'AUDIT_ONLY', 'ENABLED'])
    def test_none_command_emits_event_no_write_attempted(self, mode: str) -> None:
        pipeline = _make_no_command_pipeline()
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        policy = make_execution_policy(mode)
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder, write_execution_policy=policy,
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        event = recorder.events[0]
        assert event.triggered is True
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type is None


# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------


class TestHookDefaultPolicy:
    def test_default_policy_is_disabled(self) -> None:
        hook = MemoryRuntimeHook()
        assert hook.write_execution_policy.mode == 'DISABLED'

    def test_default_policy_does_not_dispatch(self) -> None:
        # Pipeline 产 command + 默认 DISABLED → 不调 Dispatcher
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is False
        assert dispatcher.received == []

    def test_explicit_policy_overrides_default(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        policy = make_execution_policy('ENABLED')
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            write_execution_policy=policy,
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is True
        assert dispatcher.received == [_make_command()]


# ---------------------------------------------------------------------------
# Audit event field correctness across modes
# ---------------------------------------------------------------------------


class TestAuditEventModeCorrectness:
    def test_disabled_event_has_no_error_type(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('DISABLED'),
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        event = recorder.events[0]
        # mode=DISABLED 不是错误
        assert event.error_type is None
        assert event.write_attempted is False

    def test_audit_only_event_has_no_error_type(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('AUDIT_ONLY'),
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        event = recorder.events[0]
        # mode=AUDIT_ONLY 不是错误
        assert event.error_type is None
        assert event.write_attempted is False

    def test_enabled_event_write_attempted_true(self) -> None:
        pipeline = _make_command_pipeline(_make_command())
        dispatcher = _make_recording_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        hook.after_agent_response(_make_agent_result(), CONV_ID)

        event = recorder.events[0]
        # mode=ENABLED → 实际尝试
        assert event.write_attempted is True
        assert event.write_success is True
        assert event.error_type is None
