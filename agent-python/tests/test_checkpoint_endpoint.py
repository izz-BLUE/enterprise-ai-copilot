import json
from datetime import date
from unittest.mock import Mock, patch

from fastapi import Request

from app.agents.langgraph_agent import resume_langgraph_agent, run_langgraph_agent
from app.main import app, langgraph_chat
from app.runtime.execution_recovery import RecoveryDecision, RecoveryMode
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
    runtime.inspect_recovery.return_value = RecoveryDecision(RecoveryMode.NEW_EXECUTION)
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


def test_resume_invocation_uses_none_and_current_runtime_context():
    graph = Mock()
    graph.invoke.return_value = _RESULT
    runtime_thread_id = 'rt_' + ('c' * 64) + ':planner-v1'

    result = resume_langgraph_agent(
        graph=graph,
        runtime_thread_id=runtime_thread_id,
        allow_eval=True,
        allow_business_actions=False,
        employee_id='E10001',
        business_date=date(2026, 8, 27),
        trace_id='resume-trace',
    )

    assert result == _RESULT
    assert graph.invoke.call_args.args[0] is None
    assert graph.invoke.call_args.kwargs['config'] == {
        'metadata': {'business_trace_id': 'resume-trace'},
        'configurable': {'thread_id': runtime_thread_id},
    }
    context = graph.invoke.call_args.kwargs['context']
    assert context['employee_id'] == 'E10001'
    assert context['allow_eval'] is True
    assert context['allow_business_actions'] is False
    assert context['business_date'].isoformat() == '2026-08-27'
    assert context['trace_id'] == 'resume-trace'
    assert context['deadline_monotonic'] > 0
    assert graph.invoke.call_args.kwargs['durability'] == 'sync'


def test_postgres_endpoint_resumes_without_history_hydration(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('d' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    runtime.inspect_recovery.return_value = RecoveryDecision(
        RecoveryMode.RESUME, pending_node='planner_node', execution_id='ex_' + ('a' * 32),
    )
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.resume_langgraph_agent', return_value=_RESULT) as resume, \
            patch('app.main.run_langgraph_agent') as fresh:
        response = langgraph_chat(
            ChatRequest(message='问题'),
            _request({'X-Agent-Thread-Id': 'rt_' + ('d' * 64)}),
        )

    assert response.success is True
    resume.assert_called_once()
    assert resume.call_args.kwargs['graph'] is graph
    assert resume.call_args.kwargs['runtime_thread_id'].endswith(':planner-v1')
    assert resume.call_args.kwargs['trace_id'] == 'checkpoint-trace'
    fresh.assert_not_called()
    runtime.load_execution_history.assert_not_called()
    runtime.release_thread.assert_called_once()


def test_postgres_endpoint_recovery_conflict_returns_409_without_side_effects(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('e' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    runtime.inspect_recovery.return_value = RecoveryDecision(
        RecoveryMode.CONFLICT_REQUEST,
        reason='request_mismatch',
        execution_id='ex_' + ('b' * 32),
    )
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.resume_langgraph_agent') as resume, \
            patch('app.main.run_langgraph_agent') as fresh:
        response = langgraph_chat(
            ChatRequest(message='另一个问题'),
            _request({'X-Agent-Thread-Id': 'rt_' + ('e' * 64)}),
        )

    assert response.status_code == 409
    assert response.body
    payload = json.loads(response.body)
    assert payload['category'] == 'recovery_conflict'
    assert payload['success'] is False
    assert '当前会话存在未完成的 Agent 执行' in payload['answer']
    resume.assert_not_called()
    fresh.assert_not_called()
    runtime.load_execution_history.assert_not_called()
    runtime.release_thread.assert_called_once()


def test_postgres_endpoint_resume_failure_returns_502_and_releases_guard(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('f' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    runtime.inspect_recovery.return_value = RecoveryDecision(
        RecoveryMode.RESUME, pending_node='planner_node', execution_id='ex_' + ('c' * 32),
    )
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.resume_langgraph_agent', side_effect=RuntimeError('resume failed')) as resume:
        response = langgraph_chat(
            ChatRequest(message='问题'),
            _request({'X-Agent-Thread-Id': 'rt_' + ('f' * 64)}),
        )

    assert response.status_code == 502
    resume.assert_called_once()
    runtime.load_execution_history.assert_not_called()
    runtime.release_thread.assert_called_once()
