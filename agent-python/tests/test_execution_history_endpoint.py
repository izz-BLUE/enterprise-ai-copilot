"""P3-2 endpoint same-thread guard tests."""

from unittest.mock import Mock, patch

from fastapi import Request

from app.main import app, langgraph_chat
from app.runtime.checkpoint_runtime import CheckpointRuntime
from app.schemas.chat_schema import ChatRequest


def _request(thread_id: str) -> Request:
    request = Request({
        'type': 'http',
        'method': 'POST',
        'path': '/agent/langgraph/chat',
        'headers': [
            (b'x-agent-thread-id', thread_id.encode()),
        ],
    })
    request.state.trace_id = 'concurrency-trace'
    return request


def _runtime() -> CheckpointRuntime:
    runtime = CheckpointRuntime(
        mode='POSTGRES', dsn='postgresql://unused',
        connect_timeout_seconds=1, max_connections=1,
    )
    runtime._planner_graph = Mock()
    return runtime


def test_endpoint_same_thread_busy_returns_429_and_does_not_run_agent(monkeypatch):
    base_thread_id = 'rt_' + ('a' * 64)
    final_thread_id = base_thread_id + ':planner-v1'
    runtime = _runtime()
    assert runtime.try_acquire_thread(final_thread_id) is True
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    try:
        with patch('app.main.run_langgraph_agent') as run:
            response = langgraph_chat(
                ChatRequest(message='继续'), _request(base_thread_id),
            )
        assert response.status_code == 429
        assert response.headers['Retry-After'] == '1'
        run.assert_not_called()
    finally:
        runtime.release_thread(final_thread_id)


def test_endpoint_graph_failure_releases_same_thread_guard(monkeypatch):
    base_thread_id = 'rt_' + ('b' * 64)
    runtime = _runtime()
    runtime.load_execution_history = Mock(return_value=[])
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.run_langgraph_agent', side_effect=RuntimeError('graph failed')) as run:
        response = langgraph_chat(
            ChatRequest(message='继续'), _request(base_thread_id),
        )

    assert response.status_code == 502
    run.assert_called_once()
    assert runtime.active_thread_ids == set()


def test_endpoint_checkpoint_history_read_failure_returns_503_and_releases_guard(monkeypatch):
    base_thread_id = 'rt_' + ('c' * 64)
    runtime = _runtime()
    runtime.load_execution_history = Mock(side_effect=OSError('database unavailable'))
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.run_langgraph_agent') as run:
        response = langgraph_chat(
            ChatRequest(message='继续'), _request(base_thread_id),
        )

    assert response.status_code == 503
    run.assert_not_called()
    assert runtime.active_thread_ids == set()
