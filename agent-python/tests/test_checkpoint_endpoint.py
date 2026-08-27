import json
from datetime import date
from unittest.mock import Mock, patch

from fastapi import Request

from app.agents.langgraph_agent import (
    resume_langgraph_agent,
    run_langgraph_agent,
)
from app.main import app, langgraph_chat, langgraph_hitl_resume
from app.runtime.execution_recovery import RecoveryDecision, RecoveryMode
from app.schemas.chat_schema import ChatRequest
from app.schemas.hitl_schema import HitlResumePayload


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
            _request({
                'X-Agent-Thread-Id': 'rt_' + ('d' * 64),
                'X-Employee-Id': 'E20002',
                'X-Allow-Eval': 'true',
                'X-Allow-Business-Actions': 'true',
                'X-Business-Date': '2026-08-27',
            }),
        )

    assert response.success is True
    resume.assert_called_once()
    assert resume.call_args.kwargs['graph'] is graph
    assert resume.call_args.kwargs['runtime_thread_id'].endswith(':planner-v1')
    assert resume.call_args.kwargs['trace_id'] == 'checkpoint-trace'
    runtime.inspect_recovery.assert_called_once_with(
        graph=graph,
        thread_id='rt_' + ('d' * 64) + ':planner-v1',
        question='问题',
        business_date=date(2026, 8, 27),
        employee_id='E20002',
        allow_eval=True,
        allow_business_actions=True,
    )
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


def test_postgres_endpoint_capability_conflict_hides_persisted_proposal(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('g' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    runtime.inspect_recovery.return_value = RecoveryDecision(
        RecoveryMode.CONFLICT_CAPABILITY,
        reason='business_capability_revoked',
        execution_id='ex_' + ('d' * 32),
    )
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.resume_langgraph_agent') as resume, \
            patch('app.main.run_langgraph_agent') as fresh:
        response = langgraph_chat(
            ChatRequest(message='申请报销'),
            _request({
                'X-Agent-Thread-Id': 'rt_' + ('g' * 64),
                'X-Allow-Business-Actions': 'false',
            }),
        )

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload['category'] == 'recovery_conflict'
    assert payload['action_proposal'] is None
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


def test_hitl_resume_endpoint_uses_authoritative_command_and_skips_memory(monkeypatch):
    runtime = Mock()
    graph = Mock()
    runtime.build_thread_id.return_value = 'rt_' + ('h' * 64) + ':planner-v1'
    runtime.get_graph.return_value = graph
    runtime.try_acquire_thread.return_value = True
    decision = RecoveryDecision(
        RecoveryMode.WAITING_USER,
        pending_node='approval_node',
        execution_id='ex_' + ('a' * 32),
        hitl_wait={
            'schema_version': 1,
            'kind': 'BUSINESS_ACTION_CONFIRMATION',
            'wait_id': 'wait_' + ('b' * 64),
            'execution_id': 'ex_' + ('a' * 32),
            'action_type': 'ANNUAL_LEAVE_REQUEST',
        },
    )
    payload = HitlResumePayload(
        schema_version=1,
        wait_id='wait_' + ('b' * 64),
        execution_id='ex_' + ('a' * 32),
        decision='CONFIRMED',
        action_id='act-java-001',
        action_type='ANNUAL_LEAVE_REQUEST',
        action_status='SUCCEEDED',
        request_id='LR-202608-0001',
        message='Java authoritative result',
    )
    result = {
        'answer': '已提交',
        'route': 'action',
        'safe': True,
        'category': 'business_action',
        'sources': [],
        'hitl_wait': decision.hitl_wait,
    }
    monkeypatch.setattr('app.main.LANGGRAPH_CHECKPOINT_MODE', 'POSTGRES')
    monkeypatch.setattr(app.state, 'checkpoint_runtime', runtime, raising=False)

    with patch('app.main.inspect_hitl_resume', return_value=decision), \
            patch('app.main.resume_hitl_langgraph_agent', return_value=result) as resume, \
            patch('app.main.resume_langgraph_agent') as legacy, \
            patch('app.main._build_memory_runtime_hook') as memory:
        response = langgraph_hitl_resume(
            payload,
            _request({
                'X-Agent-Thread-Id': 'rt_' + ('h' * 64),
                'X-Employee-Id': 'E10001',
                'X-Allow-Business-Actions': 'false',
                'X-Business-Date': '2026-08-27',
            }),
        )

    assert response.success is True
    assert response.hitl_wait is not None
    resume.assert_called_once()
    assert resume.call_args.kwargs['payload'] == payload
    assert resume.call_args.kwargs['allow_business_actions'] is False
    legacy.assert_not_called()
    memory.assert_not_called()
    runtime.release_thread.assert_called_once()
