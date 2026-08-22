from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.clients.java_memory_client import JavaMemoryClient, JavaMemoryClientError
from app.memory.memory_write_policy import MemoryWriteCommand


CONVERSATION_ID = 'leave-demo-01'


def command(action: str = 'UPSERT') -> MemoryWriteCommand:
    status = {'UPSERT': 'ACTIVE', 'COMPLETE': 'COMPLETED', 'ABANDON': 'ABANDONED'}[action]
    return MemoryWriteCommand(
        action=action,
        task_type='LEAVE_REQUEST',
        status=status,
        task_state={'phase': 'clarify'},
        summary='等待日期',
    )


def client(http_client: Any, **kwargs: Any) -> JavaMemoryClient:
    return JavaMemoryClient(
        http_client=http_client,
        base_url='http://java:8080',
        conversation_id=CONVERSATION_ID,
        internal_token='internal-token',
        scope_token='scope-token',
        trace_id='trace-1',
        **kwargs,
    )


def test_endpoint_and_headers_are_scoped() -> None:
    http_client = MagicMock()
    response = MagicMock(status_code=200)
    http_client.post.return_value = response

    result = client(http_client).write_memory(command())

    assert result is response
    url, = http_client.post.call_args.args
    assert url == (
        'http://java:8080/api/internal/memory/conversations/'
        'leave-demo-01/write'
    )
    kwargs = http_client.post.call_args.kwargs
    assert set(kwargs['json']) == {'action', 'taskType', 'status', 'taskState', 'summary'}
    assert kwargs['headers'] == {
        'X-Internal-Token': 'internal-token',
        'X-Memory-Write-Scope': 'scope-token',
        'X-Trace-Id': 'trace-1',
    }


def test_trailing_base_url_is_normalized_and_conversation_is_validated() -> None:
    http_client = MagicMock()
    JavaMemoryClient(http_client, 'http://java:8080///', CONVERSATION_ID)
    with pytest.raises(ValueError):
        JavaMemoryClient(http_client, 'http://java:8080', '../escape')
    with pytest.raises(ValueError):
        JavaMemoryClient(http_client, 'http://java:8080', '')


def test_identity_fields_are_not_client_parameters_or_payload_fields() -> None:
    http_client = MagicMock()
    http_client.post.return_value = MagicMock(status_code=200)
    client(http_client).write_memory(command())

    payload = http_client.post.call_args.kwargs['json']
    assert not {'userId', 'user_id', 'employeeId', 'employee_id', 'conversationId'} & set(payload)


@pytest.mark.parametrize('action', ['UPSERT', 'COMPLETE', 'ABANDON'])
def test_action_and_status_are_forwarded(action: str) -> None:
    http_client = MagicMock()
    http_client.post.return_value = MagicMock(status_code=200)

    client(http_client).write_memory(command(action))

    payload = http_client.post.call_args.kwargs['json']
    assert payload['action'] == action
    assert payload['status'] == {'UPSERT': 'ACTIVE', 'COMPLETE': 'COMPLETED', 'ABANDON': 'ABANDONED'}[action]


def test_http_status_failure_is_wrapped() -> None:
    http_client = MagicMock()
    http_client.post.return_value = MagicMock(status_code=403)

    with pytest.raises(JavaMemoryClientError):
        client(http_client).write_memory(command())


def test_http_exception_is_wrapped_and_cause_preserved() -> None:
    http_client = MagicMock()
    cause = ConnectionError('java down')
    http_client.post.side_effect = cause

    with pytest.raises(JavaMemoryClientError) as error:
        client(http_client).write_memory(command())
    assert error.value.__cause__ is cause


def test_invalid_command_is_rejected() -> None:
    http_client = MagicMock()
    with pytest.raises(TypeError):
        client(http_client).write_memory({'action': 'UPSERT'})  # type: ignore[arg-type]


def test_callable_shorthand_and_dispatcher_integration() -> None:
    http_client = MagicMock()
    http_client.post.return_value = {'ok': True}
    java_client = client(http_client)

    assert java_client(command('COMPLETE')) == {'ok': True}

    from app.memory.memory_write_dispatcher import MemoryWriteDispatcher

    dispatcher = MemoryWriteDispatcher(writer=java_client)
    assert dispatcher(command()) == {'ok': True}
    assert http_client.post.call_count == 2
