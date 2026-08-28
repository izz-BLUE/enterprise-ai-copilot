"""test_expense_proposal_tool.py —— P2-A expense_proposal_tool 测试

覆盖（V2 §十三 / §十五 / 追加约束 §1/§2/§4）：
- happy：context（travel + invoice 验真）+ 用户问题 → ExpenseActionProposal
- 主 Demo："最近一次已批准出差 + 对应发票"确定性选最新 APPROVED trip
- missing_fields：无 trip_id / 无发票 → Clarification
- deterministic 计算：HOTEL 750×晚封顶、TAXI 实报；金额由程序层算（非法
  claimed/reimbursable 不入 Proposal 结构）
- 禁止重新调 MCP：Tool 内部不调用 get_enterprise_oa_client
- context 注入：由 Executor 从 tool_history 构造
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.tools.enterprise_tools import expense_proposal_tool
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

# 模拟一份完整的已验证 context（travel + invoice）
_HAPPY_CONTEXT = {
    "travel_record": [
        {
            "trip_id": "TRIP-20260818-001",
            "employee_id": "E10001",
            "destination": "上海",
            "start_date": "2026-08-18",
            "end_date": "2026-08-20",
            "purpose": "客户拜访",
            "status": "APPROVED",
            "expense_documents": [
                {"invoice_id": "INV-001", "category": "HOTEL",
                 "declared_amount": 1600, "description": "上海如家 2 晚"},
                {"invoice_id": "INV-002", "category": "TAXI",
                 "declared_amount": 230, "description": "机场往返打车"},
            ],
        },
    ],
    "invoices": [
        {
            "success": True,
            "invoice_id": "INV-001",
            "valid": True,
            "amount": 1600,
            "category": "HOTEL",
            "duplicate": False,
        },
        {
            "success": True,
            "invoice_id": "INV-002",
            "valid": True,
            "amount": 230,
            "category": "TAXI",
            "duplicate": False,
        },
    ],
    "policy_context": "酒店每晚最高 750 元。",
}


def _invoke(question, business_date="2026-08-26", context=None, **extra):
    args = {
        "question": question,
        "business_date": business_date,
        "trace_id": "trace-exp",
        "context": context if context is not None else _HAPPY_CONTEXT,
    }
    args.update(extra)
    return json.loads(expense_proposal_tool.invoke(args))


class TestExpenseProposalHappy:
    def test_happy_proposal_structure(self):
        out = _invoke("帮我报销上周上海出差的酒店和打车费用")
        assert out["success"] is True
        assert out["kind"] == "proposal"
        assert out["missing_fields"] == []
        proposal = out["action_proposal"]
        assert proposal["action_type"] == "EXPENSE_CLAIM"
        assert proposal["trip_id"] == "TRIP-20260818-001"
        assert proposal["invoice_ids"] == ["INV-001", "INV-002"]
        assert proposal["cost_center"] == "COST-DEFAULT"

    def test_demo_prompt_selects_latest_approved_trip_and_corresponding_invoices(self):
        context = {
            "travel_record": [
                *_HAPPY_CONTEXT["travel_record"],
                {
                    "trip_id": "TRIP-20260701-002",
                    "employee_id": "E10001",
                    "destination": "北京",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-02",
                    "purpose": "总部汇报",
                    "status": "APPROVED",
                    "expense_documents": [],
                },
                {
                    "trip_id": "TRIP-20260825-003",
                    "employee_id": "E10001",
                    "destination": "深圳",
                    "start_date": "2026-08-25",
                    "end_date": "2026-08-27",
                    "purpose": "供应商洽谈",
                    "status": "PENDING",
                    "expense_documents": [
                        {"invoice_id": "INV-999", "category": "MEAL"},
                    ],
                },
            ],
            "invoices": _HAPPY_CONTEXT["invoices"],
            "policy_context": _HAPPY_CONTEXT["policy_context"],
        }
        out = _invoke(
            "请根据我最近一次已批准的出差和对应发票，帮我准备一份差旅报销申请。",
            context=context,
        )
        assert out["success"] is True
        assert out["kind"] == "proposal"
        assert out["missing_fields"] == []
        proposal = out["action_proposal"]
        assert proposal["trip_id"] == "TRIP-20260818-001"
        assert proposal["invoice_ids"] == ["INV-001", "INV-002"]

    def test_relative_invoice_selection_still_requires_verified_invoice_facts(self):
        context = {
            "travel_record": _HAPPY_CONTEXT["travel_record"],
            "invoices": [_HAPPY_CONTEXT["invoices"][0]],
            "policy_context": "",
        }
        out = _invoke(
            "请根据我最近一次已批准的出差和对应发票，帮我准备一份差旅报销申请。",
            context=context,
        )
        assert out["success"] is True
        assert out["kind"] == "clarification"
        assert "invoice_ids" in out["missing_fields"]
        assert out["action_proposal"] is None

    def test_deterministic_calculation_hotel_cap_and_taxi(self):
        """HOTEL 1600 → 封顶 750×2=1500；TAXI 230 → 实报 230；claimed 1830。"""
        out = _invoke("帮我报销上周上海出差的酒店和打车费用")
        proposal = out["action_proposal"]
        assert proposal["claimed_amount"] == "1830.00"
        assert proposal["reimbursable_amount"] == "1730.00"
        assert proposal["stay_nights"] == 2

    def test_trusted_identity_never_in_proposal(self):
        out = _invoke("帮我报销上周上海出差的酒店和打车费用")
        proposal = out["action_proposal"]
        for forbidden in ("employee_id", "user_id", "role", "permission",
                          "token", "nonce", "idempotency_key", "trace_id"):
            assert forbidden not in proposal, forbidden


class TestExpenseProposalClarification:
    def test_missing_trip_id(self):
        out = _invoke("帮我报销上周出差的酒店费用", context={
            "travel_record": [], "invoices": _HAPPY_CONTEXT["invoices"],
            "policy_context": "",
        })
        assert out["kind"] == "clarification"
        assert "trip_id" in out["missing_fields"]

    def test_missing_invoice(self):
        out = _invoke("帮我报销上周上海出差的酒店费用", context={
            "travel_record": _HAPPY_CONTEXT["travel_record"],
            "invoices": [], "policy_context": "",
        })
        assert out["kind"] == "clarification"
        assert "invoice_ids" in out["missing_fields"]

    def test_invoice_not_verified_returns_clarification(self):
        """发票存在但未验真（valid=False）→ Clarification。"""
        out = _invoke("帮我报销上海出差费用，使用发票 INV-001",
                      context={
                          "travel_record": _HAPPY_CONTEXT["travel_record"],
                          "invoices": [{
                              "success": True, "invoice_id": "INV-001",
                              "valid": False, "amount": 1600, "category": "HOTEL",
                          }],
                          "policy_context": "",
                      })
        assert out["kind"] == "clarification"
        assert "invoice_ids" in out["missing_fields"]


class TestExpenseProposalNoMcpCalls:
    def test_tool_never_touches_mcp_client(self):
        """expense_proposal_tool 内部禁止重调 MCP（V2 §十三 强制修正）。"""
        with patch("app.tools.enterprise_tools.get_enterprise_oa_client",
                   side_effect=AssertionError("不应调用 MCP client")) as mcp:
            out = _invoke("帮我报销上周上海出差的酒店和打车费用")
        assert out["success"] is True
        mcp.assert_not_called()

    def test_expense_proposal_context_injection_by_executor(self):
        """Executor 调用 expense_proposal_tool 前从 tool_history 构造 context。"""
        from app.agents.tool_executor_node import tool_executor_node

        travel_history = {
            "tool_name": "travel_record_tool",
            "arguments": {},
            "status": "success",
            "observation": json.dumps({
                "success": True,
                "items": _HAPPY_CONTEXT["travel_record"],
            }, ensure_ascii=False),
        }
        invoice_history = {
            "tool_name": "invoice_verify_tool",
            "arguments": {"invoice_id": "INV-001"},
            "status": "success",
            "observation": json.dumps({
                "success": True, "invoice_id": "INV-001", "valid": True,
                "amount": 1600, "category": "HOTEL", "duplicate": False,
            }, ensure_ascii=False),
        }
        invoice_history2 = {
            "tool_name": "invoice_verify_tool",
            "arguments": {"invoice_id": "INV-002"},
            "status": "success",
            "observation": json.dumps({
                "success": True, "invoice_id": "INV-002", "valid": True,
                "amount": 230, "category": "TAXI", "duplicate": False,
            }, ensure_ascii=False),
        }

        decision = {
            "action": "tool",
            "tool_name": "expense_proposal_tool",
            "arguments": {},
            "answer": None,
            "reason_code": "need_expense_proposal",
        }
        state = {
            "question": "帮我报销 TRIP-20260818-001 的酒店和打车费用",
            "safe": True,
            "route": "",
            "answer": "",
            "tool_result": {},
            "sources": [],
            "reason": "",
            "category": "",
            "allow_eval": False,
            "allow_business_actions": True,
            "business_date": __import__("datetime").date(2026, 8, 26),
            "trace_id": "trace-exp",
            "employee_id": "E10001",
            "action_proposal": None,
            "missing_fields": [],
            "step_count": 2,
            "tool_call_count": 2,
            "tool_history": [travel_history, invoice_history, invoice_history2],
            "observation": "",
            "planner_decision": decision,
            "stop_reason": "",
        }
        result = tool_executor_node(
            checkpoint_safe_state(state), runtime_for_state(state),
        )
        assert result["stop_reason"] == "tool_executed"
        observation = json.loads(result["observation"])
        assert observation["kind"] == "proposal"
        assert observation["action_proposal"]["trip_id"] == "TRIP-20260818-001"
        assert observation["action_proposal"]["invoice_ids"] == ["INV-001", "INV-002"]
