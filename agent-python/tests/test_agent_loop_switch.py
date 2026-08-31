"""Planner-first production entrypoint tests.

The legacy deterministic graph remains callable only as a direct test/offline
compatibility seam; the HTTP endpoint has no graph-selection switch.
"""

from unittest.mock import Mock, patch

from fastapi import Request

from app.agents import langgraph_agent
from app.main import app, langgraph_chat
from app.runtime.execution_recovery import RecoveryDecision, RecoveryMode
from app.schemas.chat_schema import ChatRequest

_RAG_RESULT = {
    "answer": "ok",
    "route": "rag",
    "safe": True,
    "category": "normal",
    "reason": "",
    "sources": [],
}


def request(headers=None):
    headers = headers or {"X-Agent-Thread-Id": "rt_" + ("a" * 64)}
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in headers.items()
    ]
    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/agent/langgraph/chat",
        "headers": raw_headers,
    })
    req.state.trace_id = headers.get("X-Trace-Id", "trace")
    return req


def _install_runtime(monkeypatch):
    runtime = Mock()
    runtime.build_thread_id.return_value = "rt_" + ("a" * 64) + ":planner-v1"
    runtime.get_graph.return_value = Mock()
    runtime.try_acquire_thread.return_value = True
    runtime.inspect_recovery.return_value = RecoveryDecision(RecoveryMode.NEW_EXECUTION)
    runtime.load_execution_history.return_value = []
    monkeypatch.setattr(app.state, "checkpoint_runtime", runtime, raising=False)
    return runtime


def test_api_always_forwards_planner_first(monkeypatch):
    _install_runtime(monkeypatch)
    with patch("app.main.run_langgraph_agent", return_value=_RAG_RESULT) as run:
        langgraph_chat(ChatRequest(message="问题"), request())
    assert run.call_args.kwargs["use_planner"] is True


def test_use_planner_true_selects_loop_graph():
    with patch.object(langgraph_agent, "build_agent_loop_graph") as loop, \
            patch.object(langgraph_agent, "build_agent_graph") as legacy:
        langgraph_agent.run_langgraph_agent("问题", use_planner=True)
    loop.assert_called_once()
    legacy.assert_not_called()


def test_legacy_graph_selection_is_direct_compatibility_only():
    with patch.object(langgraph_agent, "build_agent_loop_graph") as loop, \
            patch.object(langgraph_agent, "build_agent_graph") as legacy:
        langgraph_agent.run_langgraph_agent("问题", use_planner=False)
    legacy.assert_called_once()
    loop.assert_not_called()
