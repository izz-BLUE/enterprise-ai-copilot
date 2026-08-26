from unittest.mock import Mock, patch

from fastapi import Request

from app.agents.langgraph_agent import run_langgraph_agent
from app.main import app, langgraph_chat
from app.schemas.chat_schema import ChatRequest


def _request(headers=None):
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    request = Request({
        'type': 'http',
        'method': 'POST',
        'path': '/agent/langgraph/chat',
        'headers': raw_headers,
    })
    request.state.trace_id = 'checkpoint-trace'
    return request


_RESULT = {
    'answer': 'ok',
    'route': 'rag',
    'safe': True,
    'category': 'normal',
    'reason': '',
    'sources': [],
}


def test_postgres_endpoint_uses_startup_graph_and_java_thread_id(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('a' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.run_langgraph_agent', return_value=_RESULT) as run:
        response = langgraph_chat(
            ChatRequest(message='问题'),
            _request({'X-Agent-Thread-Id': 'rt_' + ('a' * 64)}),
        )

    assert response.success is True
    runtime.build_thread_id.assert_called_once_with('rt_' + ('a' * 64), use_planner=True)
    runtime.get_graph.assert_called_once_with(use_planner=True)
    assert run.call_args.kwargs['graph'] is graph
    assert run.call_args.kwargs['runtime_thread_id'] == 'rt_' + ('a' * 64) + ':planner-v1'


def test_postgres_endpoint_rejects_missing_thread_id_without_agent_fallback(monkeypatch):
    runtime = Mock()
    runtime.build_thread_id.side_effect = ValueError('格式无效')
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.run_langgraph_agent') as run:
        response = langgraph_chat(ChatRequest(message='问题'), _request())

    assert response.status_code == 400
    assert response.body
    run.assert_not_called()


def test_persistent_graph_invocation_uses_sync_and_keeps_trusted_fields_out_of_state():
    graph = Mock()
    graph.invoke.return_value = _RESULT
    runtime_thread_id = 'rt_' + ('b' * 64) + ':deterministic-v1'

    result = run_langgraph_agent(
        '问题',
        allow_eval=True,
        allow_business_actions=True,
        employee_id='E10001',
        trace_id='trusted-trace',
        graph=graph,
        runtime_thread_id=runtime_thread_id,
    )

    assert result == _RESULT
    initial = graph.invoke.call_args.args[0]
    assert not {
        'employee_id', 'allow_eval', 'allow_business_actions',
        'business_date', 'trace_id', 'deadline_monotonic',
    }.intersection(initial)
    assert graph.invoke.call_args.kwargs['config'] == {
        'metadata': {'business_trace_id': 'trusted-trace'},
        'configurable': {'thread_id': runtime_thread_id},
    }
    assert graph.invoke.call_args.kwargs['durability'] == 'sync'
