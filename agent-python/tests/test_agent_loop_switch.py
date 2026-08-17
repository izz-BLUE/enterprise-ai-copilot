"""AGENT_LOOP_ENABLED 开关测试。

AGENT_LOOP_ENABLED 是服务端配置开关，不暴露给客户端 header / 请求字段：
- true  → /agent/langgraph/chat 以 use_planner=True 调用 → build_agent_loop_graph（Planner Loop）
- false → use_planner=False → build_agent_graph（旧确定性 Graph，默认行为）
"""

from unittest.mock import patch

from fastapi import Request

from app.agents import langgraph_agent
from app.main import langgraph_chat
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
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/agent/langgraph/chat",
        "headers": raw_headers,
    })
    req.state.trace_id = (headers or {}).get("X-Trace-Id", "trace")
    return req


def test_api_forwards_use_planner_true_when_enabled():
    with patch("app.main.AGENT_LOOP_ENABLED", True), \
            patch("app.main.run_langgraph_agent", return_value=_RAG_RESULT) as run:
        langgraph_chat(ChatRequest(message="问题"), request())
    assert run.call_args.kwargs["use_planner"] is True


def test_api_forwards_use_planner_false_when_disabled():
    with patch("app.main.AGENT_LOOP_ENABLED", False), \
            patch("app.main.run_langgraph_agent", return_value=_RAG_RESULT) as run:
        langgraph_chat(ChatRequest(message="问题"), request())
    assert run.call_args.kwargs["use_planner"] is False


def test_use_planner_true_selects_loop_graph():
    with patch.object(langgraph_agent, "build_agent_loop_graph") as loop, \
            patch.object(langgraph_agent, "build_agent_graph") as legacy:
        langgraph_agent.run_langgraph_agent("问题", use_planner=True)
    loop.assert_called_once()
    legacy.assert_not_called()


def test_use_planner_false_selects_legacy_graph():
    with patch.object(langgraph_agent, "build_agent_loop_graph") as loop, \
            patch.object(langgraph_agent, "build_agent_graph") as legacy:
        langgraph_agent.run_langgraph_agent("问题", use_planner=False)
    legacy.assert_called_once()
    loop.assert_not_called()
