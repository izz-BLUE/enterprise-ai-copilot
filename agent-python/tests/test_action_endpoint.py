from datetime import date
from unittest.mock import patch

from fastapi import Request

from app.main import langgraph_chat
from app.schemas.chat_schema import ChatRequest


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


def test_endpoint_reads_business_action_headers_and_preserves_trace():
    req = request({
        "X-Trace-Id": "java-trace",
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
    with patch("app.main.run_langgraph_agent", return_value=result) as run, \
            patch("app.main.AGENT_LOOP_ENABLED", False):
        response = langgraph_chat(ChatRequest(message="申请一天年假，原因为私事"), req)
    run.assert_called_once_with(
        "申请一天年假，原因为私事",
        allow_eval=False,
        allow_business_actions=True,
        business_date=date(2026, 7, 16),
        trace_id="java-trace",
        use_planner=False,
    )
    assert response.traceId == "java-trace"
    assert response.missing_fields == ["start_date", "end_date"]
    assert response.action_proposal is None


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
