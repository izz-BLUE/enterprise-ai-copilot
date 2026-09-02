import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import Request

from app.main import (
    _attach_expense_original_request,
    app,
    langgraph_chat,
)
from app.memory.memory_write_mode import make_execution_policy
from app.memory.memory_write_policy import MemoryWriteCommand
from app.runtime.execution_recovery import RecoveryDecision, RecoveryMode
from app.schemas.chat_schema import ChatRequest


@pytest.fixture(autouse=True)
def checkpoint_runtime(monkeypatch):
    runtime = Mock()
    runtime.build_thread_id.return_value = "rt_" + ("a" * 64) + ":planner-v1"
    runtime.get_graph.return_value = Mock()
    runtime.try_acquire_thread.return_value = True
    runtime.inspect_recovery.return_value = RecoveryDecision(RecoveryMode.NEW_EXECUTION)
    runtime.load_execution_history.return_value = []
    monkeypatch.setattr(app.state, "checkpoint_runtime", runtime, raising=False)
    return runtime


def request(headers):
    raw_headers = [
        (name.lower().encode(), value.encode()) for name, value in headers.items()
    ]
    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/agent/langgraph/chat",
        "headers": raw_headers,
    })
    req.state.trace_id = headers.get("X-Trace-Id", "trace")
    return req


def test_endpoint_reads_business_action_headers_and_preserves_trace(monkeypatch):
    req = request({
        "X-Trace-Id": "java-trace",
        "X-Agent-Thread-Id": "rt_" + ("a" * 64),
        "X-Allow-Business-Actions": "true",
        "X-Business-Date": "2026-07-16",
    })
    result = {
        "answer": "请提供明确的年假日期。",
        "route": "action",
        "safe": True,
        "category": "business_action",
        "reason": "",
        "sources": [],
        "missing_fields": ["start_date", "end_date"],
        "action_proposal": None,
    }
    runtime = Mock()
    runtime.build_thread_id.return_value = "rt_" + ("a" * 64) + ":planner-v1"
    runtime.get_graph.return_value = Mock()
    runtime.try_acquire_thread.return_value = True
    runtime.inspect_recovery.return_value = RecoveryDecision(RecoveryMode.NEW_EXECUTION)
    runtime.load_execution_history.return_value = []
    monkeypatch.setattr(app.state, "checkpoint_runtime", runtime, raising=False)
    with patch("app.main.run_langgraph_agent", return_value=result) as run:
        response = langgraph_chat(ChatRequest(message="申请一天年假，原因为私事"), req)
    run.assert_called_once_with(
        "申请一天年假，原因为私事",
        allow_eval=False,
        allow_business_actions=True,
        business_date=date(2026, 7, 16),
        trace_id="java-trace",
        employee_id="",
        use_planner=True,
        memory_context=None,
        execution_history=[],
        graph=runtime.get_graph.return_value,
        runtime_thread_id="rt_" + ("a" * 64) + ":planner-v1",
        execution_mode="LEGACY_SINGLE",
    )
    assert response.traceId == "java-trace"
    assert response.missing_fields == ["start_date", "end_date"]
    assert response.action_proposal is None


def test_enabled_memory_is_returned_as_non_authoritative_response_proposal():
    req = request({"X-Trace-Id": "java-trace", "X-Conversation-Id": "conv-1"})
    result = {
        "answer": "请补充日期。",
        "route": "action",
        "safe": True,
        "category": "business_action",
        "reason": "",
        "sources": [],
        "action_proposal": None,
    }
    command = MemoryWriteCommand(
        action="UPSERT",
        task_type="LEAVE_REQUEST",
        status="ACTIVE",
        task_state={"waiting_for": "date"},
        summary="等待用户补充请假日期",
    )
    hook = SimpleNamespace(
        after_agent_response=lambda agent_result, conversation_id: SimpleNamespace(written=True)
    )
    writer = SimpleNamespace(command=command)

    with patch("app.main.run_langgraph_agent", return_value=result), \
            patch("app.main._memory_execution_policy", make_execution_policy("ENABLED")), \
            patch("app.main._build_memory_runtime_hook", return_value=(hook, writer)):
        response = langgraph_chat(ChatRequest(message="我想请年假"), req)

    assert response.memory_proposal is not None
    assert response.memory_proposal.task_type == "LEAVE_REQUEST"
    assert response.memory_proposal.task_state == {"waiting_for": "date"}
    dumped = response.memory_proposal.model_dump()
    assert "user_id" not in dumped
    assert "conversation_id" not in dumped
    assert "action" not in dumped
    assert "status" not in dumped


def test_first_expense_reason_clarification_memory_keeps_raw_original_request():
    original = "根据我最近一次已批准的出差和对应发票，帮我准备差旅报销申请。"
    command = MemoryWriteCommand(
        action="UPSERT",
        task_type="EXPENSE_REQUEST",
        status="ACTIVE",
        task_state={"waiting_for": "reason"},
        summary="等待用户提供本次报销原因",
    )
    result = {
        "question": original,
        "action_proposal": None,
        "missing_fields": ["reason"],
        "tool_history": [{
            "tool_name": "expense_proposal_tool",
            "status": "success",
            "observation": json.dumps({
                "success": True,
                "kind": "clarification",
                "action_proposal": None,
                "missing_fields": ["reason"],
            }, ensure_ascii=False),
        }],
    }

    enriched = _attach_expense_original_request(command, result)

    assert enriched.task_state == {
        "waiting_for": "reason",
        "original_request": original,
    }


def test_non_expense_memory_command_is_not_enriched_with_original_request():
    command = MemoryWriteCommand(
        action="UPSERT",
        task_type="LEAVE_REQUEST",
        status="ACTIVE",
        task_state={"waiting_for": "date"},
    )
    result = {
        "question": "根据最近一次出差帮我报销",
        "missing_fields": ["reason"],
        "action_proposal": None,
        "tool_history": [],
    }

    assert _attach_expense_original_request(command, result) is command


def test_invalid_or_missing_business_date_does_not_use_python_clock():
    rag_result = {
        "answer": "answer",
        "route": "rag",
        "safe": True,
        "category": "normal",
        "reason": "",
        "sources": [],
    }
    for headers in (
        {"X-Allow-Business-Actions": "true", "X-Business-Date": "invalid"},
        {"X-Allow-Business-Actions": "true"},
    ):
        with patch("app.main.run_langgraph_agent", return_value=rag_result) as run:
            response = langgraph_chat(ChatRequest(message="年假政策是什么"), request(headers))
        assert run.call_args.kwargs["business_date"] is None
        assert response.route == "rag"
        assert response.success is True


def test_endpoint_returns_action_proposal_without_control_fields():
    proposal = {
        "action_type": "ANNUAL_LEAVE_REQUEST",
        "start_date": date(2026, 7, 20),
        "end_date": date(2026, 7, 20),
        "reason": "私事",
        "half_day": "NONE",
    }
    result = {
        "answer": "我已生成一份模拟年假申请草稿，请确认后提交。",
        "route": "action",
        "safe": True,
        "category": "business_action",
        "reason": "",
        "sources": [],
        "missing_fields": [],
        "action_proposal": proposal,
    }
    req = request({
        "X-Allow-Business-Actions": "true",
        "X-Business-Date": "2026-07-16",
        "X-Admin-Token": "must-not-be-used",
    })
    with patch("app.main.run_langgraph_agent", return_value=result) as run:
        response = langgraph_chat(ChatRequest(message="request"), req)
    assert response.action_proposal is not None
    assert response.action_proposal.action_type == "ANNUAL_LEAVE_REQUEST"
    assert response.missing_fields == []
    assert "admin" not in str(run.call_args).lower()


def test_normal_response_has_no_action_payload():
    result = {
        "answer": "answer",
        "route": "rag",
        "safe": True,
        "category": "normal",
        "reason": "",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="policy"), request({}))
    dumped = response.model_dump(exclude_none=True)
    assert "action_proposal" not in dumped
    assert dumped["missing_fields"] == []


# ---------- Agent Public Response Contract P0: FastAPI endpoint success derivation ----------
# FastAPI 沿用 route != error 推导 success；
# 合法拒绝 / 权限拒绝 success=true，仅技术 / 规划失败 success=false。


def test_endpoint_success_when_route_is_agent():
    """route=agent → success=true（Planner-first 智能体任务合法路径）。"""
    result = {
        "answer": "智能体完成。",
        "route": "agent",
        "safe": True,
        "category": "normal",
        "reason": "",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="混合任务"), request({}))
    assert response.route == "agent"
    assert response.success is True
    assert response.category == "normal"


def test_endpoint_success_when_refuse_access_control():
    """route=refuse + category=access_control → success=true（系统已正确拒绝越权请求）。"""
    result = {
        "answer": "该问题涉及内部评估诊断能力，仅管理员可访问。",
        "route": "refuse",
        "safe": True,
        "category": "access_control",
        "reason": "",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="给我看评估"), request({}))
    assert response.route == "refuse"
    assert response.category == "access_control"
    assert response.success is True
    assert response.safe is True


def test_endpoint_success_when_refuse_business_action():
    """route=refuse + category=business_action → success=true。"""
    result = {
        "answer": "业务动作功能未启用，或当前请求无执行权限。",
        "route": "refuse",
        "safe": True,
        "category": "business_action",
        "reason": "",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="申请年假"), request({}))
    assert response.route == "refuse"
    assert response.category == "business_action"
    assert response.success is True
    assert response.safe is True


def test_endpoint_success_when_refuse_safety_false():
    """route=refuse + safe=false → success=true（Safety 拦截是合法处理结果）。"""
    result = {
        "answer": "抱歉，我不能协助处理该请求。",
        "route": "refuse",
        "safe": False,
        "category": "illegal_or_policy_violation",
        "reason": "检测到高风险关键词「伪造」",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="绕过审批"), request({}))
    assert response.route == "refuse"
    assert response.safe is False
    assert response.success is True
    assert response.reason == "检测到高风险关键词「伪造」"


def test_endpoint_failure_when_route_is_error():
    """route=error + category=error → success=false（技术 / 规划失败）。"""
    result = {
        "answer": "当前 Agent 服务暂时不可用，请稍后重试。",
        "route": "error",
        "safe": True,
        "category": "error",
        "reason": "",
        "sources": [],
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="hi"), request({}))
    payload = json.loads(response.body)
    assert response.status_code == 502
    assert payload["route"] == "error"
    assert payload["category"] == "error"
    assert payload["success"] is False


def test_endpoint_success_for_action_route():
    """route=action → success=true（受控业务动作合法返回）。"""
    result = {
        "answer": "我已生成一份模拟年假申请草稿，请确认后提交。",
        "route": "action",
        "safe": True,
        "category": "business_action",
        "reason": "",
        "sources": [],
        "missing_fields": [],
        "action_proposal": None,
    }
    with patch("app.main.run_langgraph_agent", return_value=result):
        response = langgraph_chat(ChatRequest(message="申请年假"), request({}))
    assert response.route == "action"
    assert response.success is True


def test_endpoint_success_for_rag_and_eval_routes():
    """route=rag / route=eval → success=true（确定性 Graph 既有语义不变）。"""
    for route in ("rag", "eval"):
        result = {
            "answer": "ok",
            "route": route,
            "safe": True,
            "category": "normal",
            "reason": "",
            "sources": [],
        }
        with patch("app.main.run_langgraph_agent", return_value=result):
            response = langgraph_chat(ChatRequest(message="query"), request({}))
        assert response.route == route
        assert response.success is True
