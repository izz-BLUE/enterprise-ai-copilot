"""Expense Legal Action Set：只验证 Planner capability masking。"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from app.agents.domain_provider_registry import DomainContext, ExpenseProvider
from app.agents.planner_node import (
    planner_node,
)
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

QUESTION = "根据我最近一次已批准的出差和对应发票，帮我准备差旅报销申请。"
RECENT_TRIP_QUESTION = "帮我报销最近这次出差"
TOOLS = [
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
]


def _travel_history(*invoice_ids: str, include_other_trip: bool = False) -> list[dict]:
    items = [{
        "trip_id": "TRIP-20260818-001",
        "destination": "上海",
        "start_date": "2026-08-18",
        "end_date": "2026-08-20",
        "status": "APPROVED",
        "expense_documents": [
            {"invoice_id": invoice_id} for invoice_id in ("INV-001", "INV-002")
        ],
    }]
    if include_other_trip:
        items.append({
            "trip_id": "TRIP-20260825-003",
            "destination": "深圳",
            "start_date": "2026-08-25",
            "end_date": "2026-08-27",
            "status": "PENDING",
            "expense_documents": [{"invoice_id": "INV-006"}],
        })
    history = [{
        "tool_name": TRAVEL_RECORD_TOOL_NAME,
        "arguments": {},
        "status": "success",
        "observation": json.dumps({"success": True, "items": items}, ensure_ascii=False),
    }]
    for invoice_id in invoice_ids:
        history.append({
            "tool_name": INVOICE_VERIFY_TOOL_NAME,
            "arguments": {"invoice_id": invoice_id},
            "status": "success",
            "observation": json.dumps({
                "success": True,
                "invoice_id": invoice_id,
                "valid": True,
            }, ensure_ascii=False),
        })
    return history


def _legal(history=None, *, reason="客户拜访", proposal=None, question=QUESTION):
    return ExpenseProvider().legal_tools(
        TOOLS,
        DomainContext(
            question=question,
            tool_history=tuple(history or []),
            request_expense_reason=reason,
            action_proposal=proposal,
        ),
    )


def test_reason_available_without_travel_facts_hides_invoice_and_proposal():
    legal = _legal()
    assert TRAVEL_RECORD_TOOL_NAME in legal
    assert INVOICE_VERIFY_TOOL_NAME not in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal


def test_selected_trip_with_pending_invoices_hides_proposal():
    legal = _legal(_travel_history())
    assert INVOICE_VERIFY_TOOL_NAME in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal


def test_recent_trip_without_approved_qualifier_keeps_invoice_prerequisite():
    legal = _legal(
        _travel_history(include_other_trip=True),
        question=RECENT_TRIP_QUESTION,
    )
    assert INVOICE_VERIFY_TOOL_NAME in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal


def test_one_verified_invoice_still_requires_invoice_tool():
    legal = _legal(_travel_history("INV-001"))
    assert INVOICE_VERIFY_TOOL_NAME in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal


def test_all_selected_trip_invoices_verified_exposes_proposal():
    legal = _legal(_travel_history("INV-001", "INV-002"))
    assert EXPENSE_PROPOSAL_TOOL_NAME in legal
    assert INVOICE_VERIFY_TOOL_NAME not in legal


def test_other_trip_invoice_does_not_complete_selected_trip_scope():
    legal = _legal(
        _travel_history("INV-006", include_other_trip=True),
    )
    assert INVOICE_VERIFY_TOOL_NAME in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal


def test_invoice_order_is_not_fixed_by_code():
    for order in (("INV-001", "INV-002"), ("INV-002", "INV-001")):
        assert EXPENSE_PROPOSAL_TOOL_NAME not in _legal(_travel_history(order[0]))
        legal = _legal(_travel_history(*order))
        assert EXPENSE_PROPOSAL_TOOL_NAME in legal


def test_existing_action_proposal_removes_expense_dependency_tools():
    legal = _legal(_travel_history("INV-001", "INV-002"), proposal={"action_type": "EXPENSE_CLAIM"})
    assert TRAVEL_RECORD_TOOL_NAME not in legal
    assert INVOICE_VERIFY_TOOL_NAME not in legal
    assert EXPENSE_PROPOSAL_TOOL_NAME not in legal
    assert RAG_TOOL_NAME in legal


def test_non_expense_capability_is_unchanged():
    history = _travel_history("INV-001", "INV-002")
    assert _legal(history, question="公司的年假制度是什么") == TOOLS


def test_missing_reason_preserves_read_only_capabilities():
    legal = _legal(reason=None)
    assert legal == TOOLS


def test_planner_prompt_uses_the_legal_action_set(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_OA_MCP_URL", "http://127.0.0.1:8100/mcp")
    monkeypatch.setattr("app.agents.planner_node.JAVA_BASE_URL", "http://java.test")
    monkeypatch.setattr("app.agents.planner_node.JAVA_INTERNAL_TOKEN", "test-token")
    raw = json.dumps({
        "action": "tool",
        "tool_name": TRAVEL_RECORD_TOOL_NAME,
        "arguments": {},
        "reason_code": "need_travel_history",
        "expense_reason": "客户拜访",
    }, ensure_ascii=False)
    raw_state = {
        "question": QUESTION,
        "step_count": 0,
        "tool_history": [],
        "observation": "",
        "execution_history": [],
        "employee_id": "E10001",
        "allow_eval": False,
        "allow_business_actions": True,
        "business_date": date(2026, 8, 26),
        "trace_id": "expense-legal-action-set",
        "request_expense_reason": "客户拜访",
        "action_proposal": None,
    }
    with patch("app.agents.planner_node.call_llm", return_value=raw) as llm:
        result = planner_node(
            checkpoint_safe_state(raw_state), runtime_for_state(raw_state)
        )
    system_prompt, user_prompt = llm.call_args.args
    assert result["planner_decision"]["tool_name"] == TRAVEL_RECORD_TOOL_NAME
    assert INVOICE_VERIFY_TOOL_NAME not in system_prompt
    assert EXPENSE_PROPOSAL_TOOL_NAME not in system_prompt
    current_tools = user_prompt.split("当前可用工具：\n", 1)[1].split(
        "\n\n已有工具调用历史：", 1
    )[0]
    assert TRAVEL_RECORD_TOOL_NAME in current_tools
    assert INVOICE_VERIFY_TOOL_NAME not in current_tools
    assert EXPENSE_PROPOSAL_TOOL_NAME not in current_tools
