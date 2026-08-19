"""AGENT_LOOP_ENABLED 开关测试。

AGENT_LOOP_ENABLED 是服务端配置开关，不暴露给客户端 header / 请求字段：
- true  → /agent/langgraph/chat 以 use_planner=True 调用 → build_agent_loop_graph（Planner Loop，默认）
- false → use_planner=False → build_agent_graph（旧确定性 Graph，显式回退）
"""

import importlib
import os

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


# ----------------------------------------------------------------------------
# 默认值回归（planner-first-runtime 切换后锁定）：
# env 未设置 ⇒ AGENT_LOOP_ENABLED 默认 True（Planner-first 默认 runtime）
# env 显式 false ⇒ AGENT_LOOP_ENABLED = False（保留 legacy 回退能力）
# env 显式 true ⇒ AGENT_LOOP_ENABLED = True（覆盖默认）
# ----------------------------------------------------------------------------


def _reload_config(monkeypatch, env_value):
    """重置 env 后重导入 config 模块，确保 AGENT_LOOP_ENABLED 按当前 env 重新解析。

    本地 .env（未跟踪副本）可能含 AGENT_LOOP_ENABLED，且 config.py 使用
    load_dotenv(override=True)，因此必须先把 dotenv.load_dotenv 屏蔽掉，
    否则 reload 会再次从 .env 灌入值。patch dotenv 模块本身（不是 app.core.config
    的局部属性），reload 重新执行 `from dotenv import load_dotenv` 时拿到的
    也是被替换后的版本。
    """
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("AGENT_LOOP_ENABLED", raising=False)
    if env_value is not None:
        monkeypatch.setenv("AGENT_LOOP_ENABLED", env_value)
    import app.core.config as cfg
    importlib.reload(cfg)
    return cfg


def test_default_agent_loop_enabled_true_when_env_unset(monkeypatch):
    cfg = _reload_config(monkeypatch, env_value=None)
    assert cfg.AGENT_LOOP_ENABLED is True


def test_default_agent_loop_enabled_false_when_env_explicit_false(monkeypatch):
    cfg = _reload_config(monkeypatch, env_value="false")
    assert cfg.AGENT_LOOP_ENABLED is False


def test_default_agent_loop_enabled_true_when_env_explicit_true(monkeypatch):
    cfg = _reload_config(monkeypatch, env_value="true")
    assert cfg.AGENT_LOOP_ENABLED is True


def test_endpoint_uses_planner_graph_when_agent_loop_enabled_true():
    """仓库部署默认 Planner-first：endpoint 在 AGENT_LOOP_ENABLED=True 时
    必须以 use_planner=True 调用 run_langgraph_agent。"""
    with patch("app.main.AGENT_LOOP_ENABLED", True), \
            patch("app.main.run_langgraph_agent", return_value=_RAG_RESULT) as run:
        langgraph_chat(ChatRequest(message="问题"), request())
    assert run.call_args.kwargs["use_planner"] is True
