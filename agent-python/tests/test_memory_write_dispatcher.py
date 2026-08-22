"""test_memory_write_dispatcher.py —— Memory Write Dispatcher 单元测试（Phase 3F）

覆盖：
  - TestDispatcherConstruction：默认 / 注入 function writer / 注入 object writer /
    非法 writer 拒绝 / 无 writer 时 dispatch 走 noop。
  - TestFunctionWriter：command 透传到 writer(command)；返回值原样返回。
  - TestObjectWriter：command 透传到 writer.write(command)；返回值原样返回。
  - TestReturnPassthrough：writer 返回 dict / string / None / 自定义对象 时
    dispatcher 不做加工。
  - TestExceptionWrapping：RuntimeError / ValueError / 自定义异常经
    MemoryWriteDispatcherError 传出，__cause__ 保留。
  - TestInputContract：None / dict / MemoryProposal / str 一律 TypeError。
  - TestRejectionOfBadInput：非法 writer（无 .write 且不可调用）构造时 TypeError。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.memory.memory_write_dispatcher import (
    MemoryWriteDispatcher,
    MemoryWriteDispatcherError,
)
from app.memory.memory_write_policy import MemoryWriteCommand
from app.schemas.memory_schema import MemoryProposal


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_command(
    *,
    action: str = 'UPSERT',
    task_type: str = 'LEAVE_REQUEST',
    status: str = 'IN_PROGRESS',
    task_state: dict[str, Any] | None = None,
    summary: str = '申请 2026-09-01 年假',
) -> MemoryWriteCommand:
    return MemoryWriteCommand(
        action=action,
        task_type=task_type,
        status=status,
        task_state=task_state if task_state is not None else {'phase': 'clarify'},
        summary=summary,
    )


def _make_proposal(
    *,
    action: str = 'UPSERT',
    task_type: str = 'LEAVE_REQUEST',
    status: str = 'ACTIVE',
    task_state: dict[str, Any] | None = None,
    summary: str = '申请 2026-09-01 年假',
) -> MemoryProposal:
    return MemoryProposal(
        action=action,
        task_type=task_type,
        status=status,
        task_state=task_state if task_state is not None else {'phase': 'clarify'},
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestDispatcherConstruction:
    def test_default_dispatcher(self) -> None:
        dispatcher = MemoryWriteDispatcher()
        assert dispatcher.writer is None

    def test_inject_function_writer(self) -> None:
        def writer(cmd: MemoryWriteCommand) -> str:
            return cmd.action

        dispatcher = MemoryWriteDispatcher(writer=writer)
        assert dispatcher.writer is writer

    def test_inject_object_writer(self) -> None:
        class ObjectWriter:
            def write(self, cmd: MemoryWriteCommand) -> str:
                return cmd.action

        writer = ObjectWriter()
        dispatcher = MemoryWriteDispatcher(writer=writer)
        assert dispatcher.writer is writer

    def test_inject_callable_object_writer(self) -> None:
        # 实现了 __call__ 的对象按函数处理
        class CallableWriter:
            def __call__(self, cmd: MemoryWriteCommand) -> str:
                return cmd.action

        writer = CallableWriter()
        dispatcher = MemoryWriteDispatcher(writer=writer)
        assert dispatcher.writer is writer

    def test_reject_invalid_writer(self) -> None:
        class BadWriter:
            pass

        with pytest.raises(TypeError) as exc:
            MemoryWriteDispatcher(writer=BadWriter())
        assert 'MemoryWriteDispatcher.writer' in str(exc.value)

    def test_reject_string_as_writer(self) -> None:
        # str 不可调用且无 .write，明显是用户错误
        with pytest.raises(TypeError):
            MemoryWriteDispatcher(writer='not-a-writer')  # type: ignore[arg-type]

    def test_dispatch_with_no_writer_returns_none(self) -> None:
        dispatcher = MemoryWriteDispatcher()
        result = dispatcher.dispatch(_make_command())
        assert result is None


# ---------------------------------------------------------------------------
# Function writer
# ---------------------------------------------------------------------------


class TestFunctionWriter:
    def test_command_passed_to_function_writer(self) -> None:
        received: list[MemoryWriteCommand] = []

        def writer(cmd: MemoryWriteCommand) -> str:
            received.append(cmd)
            return 'ok'

        dispatcher = MemoryWriteDispatcher(writer=writer)
        cmd = _make_command()
        result = dispatcher.dispatch(cmd)

        assert result == 'ok'
        assert received == [cmd]
        # dispatcher 不修改 command
        assert received[0].action == 'UPSERT'
        assert received[0].task_type == 'LEAVE_REQUEST'

    def test_lambda_writer(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: f'saw={c.action}')
        result = dispatcher.dispatch(_make_command())
        assert result == 'saw=UPSERT'


# ---------------------------------------------------------------------------
# Object writer
# ---------------------------------------------------------------------------


class TestObjectWriter:
    def test_command_passed_to_write_method(self) -> None:
        received: list[MemoryWriteCommand] = []

        class ObjectWriter:
            def write(self, cmd: MemoryWriteCommand) -> dict[str, Any]:
                received.append(cmd)
                return {'status': 'accepted', 'task_type': cmd.task_type}

        writer = ObjectWriter()
        dispatcher = MemoryWriteDispatcher(writer=writer)
        cmd = _make_command()
        result = dispatcher.dispatch(cmd)

        assert received == [cmd]
        assert result == {'status': 'accepted', 'task_type': 'LEAVE_REQUEST'}

    def test_write_method_returning_none(self) -> None:
        class ObjectWriter:
            def write(self, cmd: MemoryWriteCommand) -> None:
                return None

        result = MemoryWriteDispatcher(writer=ObjectWriter()).dispatch(_make_command())
        assert result is None

    def test_write_method_returning_custom_object(self) -> None:
        class WriteResult:
            def __init__(self, action: str) -> None:
                self.action = action

        class ObjectWriter:
            def write(self, cmd: MemoryWriteCommand) -> WriteResult:
                return WriteResult(cmd.action)

        result = MemoryWriteDispatcher(writer=ObjectWriter()).dispatch(_make_command())
        assert isinstance(result, WriteResult)
        assert result.action == 'UPSERT'


# ---------------------------------------------------------------------------
# Return passthrough
# ---------------------------------------------------------------------------


class TestReturnPassthrough:
    def test_writer_returns_dict(self) -> None:
        payload = {'jti': 'abc-123', 'created': True}
        dispatcher = MemoryWriteDispatcher(writer=lambda c: payload)
        result = dispatcher.dispatch(_make_command())
        assert result is payload

    def test_writer_returns_string(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: 'persist-ok')
        result = dispatcher.dispatch(_make_command())
        assert result == 'persist-ok'

    def test_writer_returns_none(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        result = dispatcher.dispatch(_make_command())
        assert result is None

    def test_writer_returns_json_string(self) -> None:
        # dispatcher 不做 json.loads —— 原样返回
        encoded = json.dumps({'ok': True})
        dispatcher = MemoryWriteDispatcher(writer=lambda c: encoded)
        result = dispatcher.dispatch(_make_command())
        assert result == encoded
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Exception wrapping
# ---------------------------------------------------------------------------


class TestExceptionWrapping:
    def test_writer_runtime_error_wrapped(self) -> None:
        def writer(cmd: MemoryWriteCommand) -> None:
            raise RuntimeError('java unavailable')

        dispatcher = MemoryWriteDispatcher(writer=writer)
        with pytest.raises(MemoryWriteDispatcherError) as exc:
            dispatcher.dispatch(_make_command())
        assert isinstance(exc.value.__cause__, RuntimeError)
        assert 'java unavailable' in str(exc.value.__cause__)

    def test_writer_value_error_wrapped(self) -> None:
        def writer(cmd: MemoryWriteCommand) -> None:
            raise ValueError('invalid payload')

        dispatcher = MemoryWriteDispatcher(writer=writer)
        with pytest.raises(MemoryWriteDispatcherError) as exc:
            dispatcher.dispatch(_make_command())
        assert isinstance(exc.value.__cause__, ValueError)
        assert 'invalid payload' in str(exc.value.__cause__)

    def test_writer_custom_exception_wrapped(self) -> None:
        class JavaConnectionLost(Exception):
            pass

        def writer(cmd: MemoryWriteCommand) -> None:
            raise JavaConnectionLost('connection reset')

        dispatcher = MemoryWriteDispatcher(writer=writer)
        with pytest.raises(MemoryWriteDispatcherError) as exc:
            dispatcher.dispatch(_make_command())
        assert isinstance(exc.value.__cause__, JavaConnectionLost)
        assert 'connection reset' in str(exc.value.__cause__)

    def test_writer_keyboard_interrupt_not_wrapped(self) -> None:
        # BaseException 不应被吞
        def writer(cmd: MemoryWriteCommand) -> None:
            raise KeyboardInterrupt()

        dispatcher = MemoryWriteDispatcher(writer=writer)
        with pytest.raises(KeyboardInterrupt):
            dispatcher.dispatch(_make_command())

    def test_object_writer_exception_wrapped(self) -> None:
        class FailingWriter:
            def write(self, cmd: MemoryWriteCommand) -> None:
                raise RuntimeError('write failed')

        dispatcher = MemoryWriteDispatcher(writer=FailingWriter())
        with pytest.raises(MemoryWriteDispatcherError) as exc:
            dispatcher.dispatch(_make_command())
        assert isinstance(exc.value.__cause__, RuntimeError)

    def test_cause_chain_preserved_arbitrary_exception(self) -> None:
        def writer(cmd: MemoryWriteCommand) -> None:
            raise OSError('network down')

        dispatcher = MemoryWriteDispatcher(writer=writer)
        with pytest.raises(MemoryWriteDispatcherError) as exc:
            dispatcher.dispatch(_make_command())
        assert isinstance(exc.value.__cause__, OSError)
        assert exc.value.__cause__.errno is None  # OSError without errno


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


class TestInputContract:
    def test_reject_none(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher.dispatch(None)  # type: ignore[arg-type]

    def test_reject_dict(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher.dispatch({'action': 'UPSERT'})  # type: ignore[arg-type]

    def test_reject_memory_proposal(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        proposal = _make_proposal()
        with pytest.raises(TypeError):
            dispatcher.dispatch(proposal)  # type: ignore[arg-type]

    def test_reject_string(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher.dispatch('UPSERT')  # type: ignore[arg-type]

    def test_reject_int(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher.dispatch(42)  # type: ignore[arg-type]

    def test_reject_base_model_subclass_but_not_command(self) -> None:
        # 即使是 Pydantic 模型，非 MemoryWriteCommand 也要拒绝
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher.dispatch(_make_proposal())  # type: ignore[arg-type]

    def test_call_shorthand_also_enforces_contract(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: None)
        with pytest.raises(TypeError):
            dispatcher(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# __call__ 行为
# ---------------------------------------------------------------------------


class TestCallShorthand:
    def test_call_shorthand_dispatches(self) -> None:
        seen: list[str] = []

        def writer(cmd: MemoryWriteCommand) -> None:
            seen.append(cmd.action)

        dispatcher = MemoryWriteDispatcher(writer=writer)
        dispatcher(_make_command(action='COMPLETE'))
        dispatcher(_make_command(action='ABANDON'))
        assert seen == ['COMPLETE', 'ABANDON']

    def test_call_shorthand_returns_writer_value(self) -> None:
        dispatcher = MemoryWriteDispatcher(writer=lambda c: c.task_type)
        assert dispatcher(_make_command(task_type='LEAVE_REQUEST')) == 'LEAVE_REQUEST'


# ---------------------------------------------------------------------------
# End-to-end Dispatcher → no writer 路径
# ---------------------------------------------------------------------------


class TestNoWriterPath:
    def test_no_writer_dispatch_returns_none_for_all_actions(self) -> None:
        dispatcher = MemoryWriteDispatcher()
        for action in ('UPSERT', 'COMPLETE', 'ABANDON'):
            assert dispatcher.dispatch(_make_command(action=action)) is None

    def test_no_writer_still_validates_input_type(self) -> None:
        dispatcher = MemoryWriteDispatcher()
        with pytest.raises(TypeError):
            dispatcher.dispatch('not a command')  # type: ignore[arg-type]
