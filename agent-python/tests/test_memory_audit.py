"""test_memory_audit.py —— Memory Audit / Observability 测试（Phase 4D）

覆盖：
  - TestEventSchema：MemoryAuditEvent extra='forbid' + 字段白名单。
  - TestForbiddenFields：所有禁止字段（user_id / employee_id / conversation_id /
    token / jwt / summary / task_state / message 等）被 schema 拒绝。
  - TestLoggingRecorder：默认 recorder 记录事件 + 写日志 + 内存累计。
  - TestRecorderProtocol：MemoryAuditRecorder Protocol 类型检查。
  - TestHookAuditIntegration：MemoryRuntimeHook 集成 audit_recorder：
    - 触发 / 未触发 / 写成功 / 写失败 / Pipeline 失败 / Dispatcher 失败 全路径
      均有 audit event 上报；
    - audit_recorder 失败不阻断返回；
    - agent_result 不被修改。
  - TestPrivacyBoundary：audit event 序列化后不含敏感字段。
  - TestErrorTypeExtraction：error_type_name 提取异常类名。
  - TestSafeProposalExtraction：safe_proposal_action / safe_task_type 兼容 None / 非法。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.memory.memory_audit import (
    LoggingAuditRecorder,
    MemoryAuditEvent,
    MemoryAuditRecorder,
    error_type_name,
    safe_proposal_action,
    safe_task_type,
)
from app.memory.memory_pipeline import (
    MemoryPipeline,
    MemoryPipelineError,
    MemoryPipelineResult,
)
from app.memory.memory_runtime_hook import MemoryRuntimeHook, MemoryRuntimeResult
from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_mode import make_execution_policy
from app.memory.memory_write_policy import MemoryWriteCommand
from app.schemas.memory_schema import MemoryProposal


CONV_ID = '11111111-1111-1111-1111-111111111111'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_agent_result(
    *,
    action_proposal: dict[str, Any] | None = None,
    tool_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'question': '申请 2026-09-01 年假',
        'answer': '好的。',
        'route': 'leave_request',
        'safe': True,
        'category': 'leave',
        'tool_history': tool_history or [],
        'action_proposal': action_proposal,
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
    result = MemoryPipelineResult(
        triggered=triggered,
        proposal=proposal,
        command=command,
        trigger_reason=trigger_reason,
    )

    class MockPipeline(MemoryPipeline):
        def __init__(self) -> None:
            self._raise = raise_exception
            self._result = result

        def process(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
            if self._raise is not None:
                raise self._raise
            return self._result

    return MockPipeline()


def _make_dispatcher(
    *,
    raise_on_dispatch: BaseException | None = None,
    return_value: Any = None,
) -> MemoryWriteDispatcher:
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
# Event schema
# ---------------------------------------------------------------------------


class TestEventSchema:
    def test_minimum_event(self) -> None:
        event = MemoryAuditEvent(triggered=False)
        assert event.triggered is False
        assert event.trigger_reason == ''
        assert event.proposal_action is None
        assert event.task_type is None
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type is None

    def test_full_event(self) -> None:
        event = MemoryAuditEvent(
            triggered=True,
            trigger_reason='action_proposal',
            proposal_action='UPSERT',
            task_type='LEAVE_REQUEST',
            write_attempted=True,
            write_success=True,
            error_type=None,
        )
        assert event.write_success is True

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            MemoryAuditEvent(  # type: ignore[call-arg]
                triggered=False,
                extra_field='not allowed',
            )


# ---------------------------------------------------------------------------
# Forbidden fields (privacy boundary)
# ---------------------------------------------------------------------------


class TestForbiddenFields:
    @pytest.mark.parametrize('forbidden_field', [
        'user_id',
        'userId',
        'employee_id',
        'employeeId',
        'conversation_id',
        'conversationId',
        'token',
        'jwt',
        'summary',
        'task_state',
        'taskState',
        'message',
        'question',
        'answer',
        'role',
        'permission',
        'allow_eval',
        'allow_business_actions',
    ])
    def test_forbidden_field_rejected(self, forbidden_field: str) -> None:
        with pytest.raises(ValidationError):
            MemoryAuditEvent(  # type: ignore[call-arg]
                triggered=True,
                **{forbidden_field: 'sensitive-value'},
            )


# ---------------------------------------------------------------------------
# Logging recorder
# ---------------------------------------------------------------------------


class TestLoggingRecorder:
    def test_default_construction(self) -> None:
        recorder = LoggingAuditRecorder()
        assert recorder.events == []

    def test_record_event(self) -> None:
        recorder = LoggingAuditRecorder()
        event = MemoryAuditEvent(triggered=True, task_type='LEAVE_REQUEST')
        recorder.record(event)
        assert recorder.events == [event]

    def test_record_multiple_events(self) -> None:
        recorder = LoggingAuditRecorder()
        e1 = MemoryAuditEvent(triggered=False)
        e2 = MemoryAuditEvent(triggered=True, write_success=True)
        recorder.record(e1)
        recorder.record(e2)
        assert recorder.events == [e1, e2]

    def test_events_returns_snapshot(self) -> None:
        recorder = LoggingAuditRecorder()
        recorder.record(MemoryAuditEvent(triggered=False))
        snapshot = recorder.events
        recorder.record(MemoryAuditEvent(triggered=True))
        # snapshot 仍只包含第一轮 event
        assert len(snapshot) == 1

    def test_clear(self) -> None:
        recorder = LoggingAuditRecorder()
        recorder.record(MemoryAuditEvent(triggered=False))
        recorder.record(MemoryAuditEvent(triggered=True))
        recorder.clear()
        assert recorder.events == []

    def test_record_does_not_swallow_exceptions(self) -> None:
        # 默认 recorder 应忠实传递业务字段，不修改 event
        recorder = LoggingAuditRecorder()
        event = MemoryAuditEvent(
            triggered=True,
            proposal_action='UPSERT',
            task_type='LEAVE_REQUEST',
            write_attempted=True,
            write_success=True,
        )
        recorder.record(event)
        # 读出来还是一致
        assert recorder.events[0].proposal_action == 'UPSERT'
        assert recorder.events[0].task_type == 'LEAVE_REQUEST'


# ---------------------------------------------------------------------------
# Protocol satisfiability
# ---------------------------------------------------------------------------


class TestRecorderProtocol:
    def test_logging_recorder_satisfies_protocol(self) -> None:
        recorder = LoggingAuditRecorder()
        assert isinstance(recorder, MemoryAuditRecorder)

    def test_custom_recorder_satisfies_protocol(self) -> None:
        class MyRecorder:
            def record(self, event: MemoryAuditEvent) -> None:
                pass

        assert isinstance(MyRecorder(), MemoryAuditRecorder)

    def test_non_recorder_does_not_satisfy_protocol(self) -> None:
        class NotARecorder:
            pass

        assert not isinstance(NotARecorder(), MemoryAuditRecorder)


# ---------------------------------------------------------------------------
# Hook integration
# ---------------------------------------------------------------------------


class TestHookAuditIntegration:
    def test_trigger_false_emits_event(self) -> None:
        pipeline = _make_pipeline(triggered=False, trigger_reason='no_trigger')
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is False
        assert event.trigger_reason == 'no_trigger'
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type is None

    def test_triggered_no_command_emits_event(self) -> None:
        pipeline = _make_pipeline(
            triggered=True,
            command=None,
            proposal=_make_proposal(action='NONE'),
            trigger_reason='action_proposal',
        )
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is True
        assert result.written is False
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is True
        assert event.proposal_action == 'NONE'
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type is None

    def test_write_success_emits_event(self) -> None:
        command = _make_command()
        proposal = _make_proposal()
        pipeline = _make_pipeline(triggered=True, command=command, proposal=proposal)
        dispatcher = _make_dispatcher(return_value={'jti': 'ok'})
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is True
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is True
        assert event.proposal_action == 'UPSERT'
        assert event.task_type == 'LEAVE_REQUEST'
        assert event.write_attempted is True
        assert event.write_success is True
        assert event.error_type is None

    def test_write_failure_emits_event(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command(),
                                   proposal=_make_proposal())
        dispatcher = _make_dispatcher(
            raise_on_dispatch=MemoryWriteDispatcherError('java down'),
        )
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is False
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is True
        assert event.write_attempted is True
        assert event.write_success is False
        assert event.error_type == 'MemoryWriteDispatcherError'

    def test_pipeline_failure_emits_event(self) -> None:
        pipeline = _make_pipeline(raise_exception=MemoryPipelineError('boom'))
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.triggered is False
        assert event.trigger_reason == 'pipeline_error'
        assert event.write_attempted is False
        assert event.write_success is False
        assert event.error_type == 'MemoryPipelineError'

    def test_pipeline_unexpected_exception_emits_event(self) -> None:
        pipeline = _make_pipeline(raise_exception=RuntimeError('crash'))
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.triggered is False
        assert len(recorder.events) == 1
        # Pipeline 兜底会包成 MemoryPipelineError
        assert recorder.events[0].error_type == 'MemoryPipelineError'

    def test_dispatcher_unexpected_exception_emits_event(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher(raise_on_dispatch=OSError('network'))
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is False
        assert len(recorder.events) == 1
        # Dispatcher 兜底会包成 MemoryWriteDispatcherError
        assert recorder.events[0].error_type == 'MemoryWriteDispatcherError'


# ---------------------------------------------------------------------------
# Audit failure does not break response
# ---------------------------------------------------------------------------


class TestAuditFailureDoesNotBreakResponse:
    def test_recorder_runtime_error_does_not_propagate(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()

        class BrokenRecorder:
            def record(self, event: MemoryAuditEvent) -> None:
                raise RuntimeError('audit infra down')

        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=BrokenRecorder(),
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        # 不抛错
        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is True

    def test_recorder_returns_normally_on_recording_failure(self) -> None:
        pipeline = _make_pipeline(triggered=False)
        dispatcher = _make_dispatcher()

        class BrokenRecorder:
            def record(self, event: MemoryAuditEvent) -> None:
                raise ValueError('serialization failed')

        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=BrokenRecorder())

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)
        assert result.triggered is False

    def test_recorder_failure_does_not_block_dispatcher(self) -> None:
        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()

        class BrokenRecorder:
            def __init__(self) -> None:
                self.calls = 0

            def record(self, event: MemoryAuditEvent) -> None:
                self.calls += 1
                raise RuntimeError('boom')

        recorder = BrokenRecorder()
        hook = MemoryRuntimeHook(
            pipeline=pipeline, dispatcher=dispatcher,
            audit_recorder=recorder,
            write_execution_policy=make_execution_policy('ENABLED'),
        )

        result = hook.after_agent_response(_make_agent_result(), CONV_ID)

        assert result.written is True
        assert recorder.calls == 1  # recorder 仍被调用过
        assert dispatcher.received == [_make_command()]


# ---------------------------------------------------------------------------
# agent_result unchanged
# ---------------------------------------------------------------------------


class TestAgentResultUnchanged:
    def test_agent_result_not_modified_on_audit_emission(self) -> None:
        original = _make_agent_result()
        snapshot = dict(original)

        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        hook.after_agent_response(original, CONV_ID)

        assert original == snapshot

    def test_agent_result_not_modified_on_recorder_failure(self) -> None:
        original = _make_agent_result()
        snapshot = dict(original)

        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()

        class BrokenRecorder:
            def record(self, event: MemoryAuditEvent) -> None:
                raise RuntimeError('fail')

        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=BrokenRecorder())

        hook.after_agent_response(original, CONV_ID)

        assert original == snapshot


# ---------------------------------------------------------------------------
# Privacy boundary — 序列化校验
# ---------------------------------------------------------------------------


class TestPrivacyBoundary:
    def test_no_sensitive_fields_in_serialized_event(self) -> None:
        recorder = LoggingAuditRecorder()
        recorder.record(MemoryAuditEvent(
            triggered=True,
            proposal_action='UPSERT',
            task_type='LEAVE_REQUEST',
            write_attempted=True,
            write_success=True,
        ))
        event = recorder.events[0]
        serialized = json.dumps(event.model_dump())
        forbidden_substrings = [
            'userId', 'user_id',
            'employeeId', 'employee_id',
            'conversationId', 'conversation_id',
            'token', 'jwt',
            'summary', 'taskState', 'task_state',
            'message', 'question', 'answer',
        ]
        for forbidden in forbidden_substrings:
            assert forbidden not in serialized, (
                f'audit event 不得包含敏感字段 {forbidden}'
            )

    def test_no_source_message_in_event(self) -> None:
        # 即便 agent_result 含敏感字段，event 也不携带
        sensitive = 'Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature'
        agent_result = _make_agent_result()
        agent_result['user_message'] = sensitive

        pipeline = _make_pipeline(triggered=True, command=_make_command())
        dispatcher = _make_dispatcher()
        recorder = LoggingAuditRecorder()
        hook = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher,
                                  audit_recorder=recorder)

        hook.after_agent_response(agent_result, CONV_ID)

        event = recorder.events[0]
        serialized = json.dumps(event.model_dump())
        assert 'Bearer' not in serialized
        assert 'eyJhbGciOiJIUzI1NiJ9' not in serialized


# ---------------------------------------------------------------------------
# error_type_name helper
# ---------------------------------------------------------------------------


class TestErrorTypeExtraction:
    def test_none_returns_none(self) -> None:
        assert error_type_name(None) is None

    def test_runtime_error_returns_class_name(self) -> None:
        assert error_type_name(RuntimeError('boom')) == 'RuntimeError'

    def test_custom_error_returns_class_name(self) -> None:
        class MyCustomError(Exception):
            pass

        assert error_type_name(MyCustomError('x')) == 'MyCustomError'

    def test_pipeline_error_returns_class_name(self) -> None:
        assert error_type_name(MemoryPipelineError('boom')) == 'MemoryPipelineError'


# ---------------------------------------------------------------------------
# safe_proposal_action / safe_task_type helpers
# ---------------------------------------------------------------------------


class TestSafeProposalExtraction:
    def test_none_returns_none(self) -> None:
        assert safe_proposal_action(None) is None
        assert safe_task_type(None) is None

    def test_real_proposal(self) -> None:
        proposal = _make_proposal(action='UPSERT', task_type='LEAVE_REQUEST')
        assert safe_proposal_action(proposal) == 'UPSERT'
        assert safe_task_type(proposal) == 'LEAVE_REQUEST'

    def test_none_action_in_proposal(self) -> None:
        proposal = _make_proposal(action='NONE')
        assert safe_proposal_action(proposal) == 'NONE'

    def test_dict_rejected(self) -> None:
        # 防御：免得 dict 误传
        assert safe_proposal_action({'action': 'UPSERT'}) is None
        assert safe_task_type({'task_type': 'LEAVE_REQUEST'}) is None

    def test_object_without_action(self) -> None:
        class Bare:
            pass

        assert safe_proposal_action(Bare()) is None
        assert safe_task_type(Bare()) is None

    def test_non_string_action(self) -> None:
        class WeirdProposal:
            action = 42  # type: ignore[assignment]
            task_type = None

        assert safe_proposal_action(WeirdProposal()) is None
        assert safe_task_type(WeirdProposal()) is None


# ---------------------------------------------------------------------------
# Default recorder wiring
# ---------------------------------------------------------------------------


class TestDefaultRecorder:
    def test_default_hook_uses_logging_recorder(self) -> None:
        hook = MemoryRuntimeHook()
        assert isinstance(hook.audit_recorder, LoggingAuditRecorder)

    def test_default_hook_records_event(self) -> None:
        hook = MemoryRuntimeHook()
        pipeline = _make_pipeline(triggered=False, trigger_reason='no_trigger')
        dispatcher = _make_dispatcher()
        # 注入 pipeline 后 recorder 仍保持默认
        hook_with_pipeline = MemoryRuntimeHook(pipeline=pipeline, dispatcher=dispatcher)
        hook_with_pipeline.after_agent_response(_make_agent_result(), CONV_ID)
        assert isinstance(hook_with_pipeline.audit_recorder, LoggingAuditRecorder)
        assert len(hook_with_pipeline.audit_recorder.events) == 1
