"""test_expense_workflow_integration.py —— P2-A Planner 选择 + Stress A-H 综合测试

不依赖真实 LLM / MCP server；通过注入 mock call_llm（Planner 决策序列）与
mock Tool 实现驱动 Agent Loop（run_langgraph_agent / tool_executor_node /
planner_node），验证：
- V2 §二十九 Planner 6 case（travel / rag / invoice / multi-step /
  status / 旧 leave memory 不干扰）
- V2 §二十八 Stress A / C / F（B / D / E / G / H 已分别在 MCP / memory /
  Java 测试中覆盖）
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from app.agents.langgraph_agent import run_langgraph_agent


@pytest.fixture(autouse=True)
def _enabled_oamcp_url(monkeypatch):
    """travel/invoice 可见性依赖 ENTERPRISE_OA_MCP_URL（V2 §三 visibility_gate）。"""
    monkeypatch.setenv("ENTERPRISE_OA_MCP_URL", "http://127.0.0.1:8100/mcp")
    monkeypatch.setattr(
        "app.agents.planner_node.JAVA_BASE_URL",
        "http://127.0.0.1:8080",
    )
    monkeypatch.setattr(
        "app.agents.planner_node.JAVA_INTERNAL_TOKEN",
        "test-internal-token",
    )
    yield

TRAVEL_ANSWER = json.dumps({
    "success": True,
    "items": [{
        "trip_id": "TRIP-20260818-001",
        "employee_id": "E10001",
        "destination": "上海",
        "start_date": "2026-08-18",
        "end_date": "2026-08-20",
        "purpose": "客户拜访",
        "status": "APPROVED",
        "expense_documents": [
            {"invoice_id": "INV-001", "category": "HOTEL",
             "declared_amount": 1600, "description": "如家 2 晚"},
            {"invoice_id": "INV-002", "category": "TAXI",
             "declared_amount": 230, "description": "机场打车"},
        ],
    }],
}, ensure_ascii=False)

INVOICE_ANSWER = json.dumps({
    "success": True, "invoice_id": "INV-001", "valid": True,
    "amount": 1600, "category": "HOTEL", "duplicate": False,
}, ensure_ascii=False)

RAG_ANSWER = json.dumps({
    "success": True, "answer": "酒店每晚最高 750 元。",
    "sources": ["hr/expense-policy.md"],
}, ensure_ascii=False)


def _tool(tool_name, arguments, reason):
    return json.dumps({
        "action": "tool", "tool_name": tool_name,
        "arguments": arguments, "reason_code": reason,
    }, ensure_ascii=False)


def _finish(answer):
    return json.dumps({
        "action": "finish", "answer": answer, "reason_code": "task_complete",
    }, ensure_ascii=False)


def _run_with_history(question, tool_history, *, employee_id="E10001",
                      memory_context=None):
    """以已有 tool_history 预置状态启动 Agent（模拟跨步骤恢复需在当前请求内）。"""
    return run_langgraph_agent(
        question, use_planner=True,
        employee_id=employee_id,
        allow_business_actions=True,
        business_date=date(2026, 8, 26),
        memory_context=memory_context,
    )


class TestPlannerSelection:
    """V2 §二十九：Planner 必须能区分 4 个新 Tool。"""

    def test_travel_record_selection(self):
        """Case 1：'我上周有哪些出差？' → travel_record_tool。"""
        decisions = [
            _tool("travel_record_tool", {}, "need_travel_history"),
            _finish("上周上海出差 8.18-8.20。"),
        ]
        with patch("app.agents.planner_node.call_llm",
                   side_effect=decisions), \
             patch("app.agents.tool_executor_node.travel_record_tool") as tk:
            tk.invoke.return_value = TRAVEL_ANSWER
            result = run_langgraph_agent(
                "我上周有哪些出差？", use_planner=True, employee_id="E10001")
        assert result["route"] == "rag" or result["route"] == "agent"
        assert any(h["tool_name"] == "travel_record_tool"
                   and h["status"] == "success" for h in result["tool_history"])

    def test_rag_selection_for_policy(self):
        """Case 2：'公司上海出差酒店报销标准是多少？' → rag_answer_tool。"""
        decisions = [
            _tool("rag_answer_tool", {"question": "上海出差酒店报销标准"},
                  "need_knowledge"),
            _finish("酒店每晚最高 750 元。"),
        ]
        with patch("app.agents.planner_node.call_llm",
                   side_effect=decisions), \
             patch("app.agents.tool_executor_node.rag_answer_tool") as tk:
            tk.invoke.return_value = RAG_ANSWER
            result = run_langgraph_agent(
                "公司上海出差酒店报销标准是多少？", use_planner=True,
                employee_id="E10001")
        assert any(h["tool_name"] == "rag_answer_tool"
                   and h["status"] == "success" for h in result["tool_history"])

    def test_status_tool_selection(self):
        """Case 5：'我那笔报销现在审批到哪了？' → expense_status_tool。"""
        decisions = [
            _tool("expense_status_tool", {"expense_id": "EXP-20260826-000001"},
                  "need_expense_status"),
            _finish("报销单 EXP-20260826-000001 状态：SUBMITTED。"),
        ]
        with patch("app.agents.planner_node.call_llm",
                   side_effect=decisions), \
             patch("app.agents.tool_executor_node.expense_status_tool") as tk:
            tk.invoke.return_value = json.dumps({
                "success": True, "expense_id": "EXP-20260826-000001",
                "status": "SUBMITTED",
            }, ensure_ascii=False)
            result = run_langgraph_agent(
                "我那笔报销 EXP-20260826-000001 现在审批到哪了？",
                use_planner=True, employee_id="E10001")
        assert any(h["tool_name"] == "expense_status_tool"
                   and h["status"] == "success" for h in result["tool_history"])

    def test_multi_step_expense_chain(self):
        """Case 4：'帮我报销上周上海出差的酒店和打车费用' → travel→invoice→proposal。"""
        decisions = [
            _tool("travel_record_tool", {}, "need_travel_history"),
            _tool("invoice_verify_tool", {"invoice_id": "INV-001"},
                  "need_invoice_verify"),
            _tool("expense_proposal_tool", {}, "need_expense_proposal"),
            _finish("已生成报销申请草稿，请确认后提交。"),
        ]
        with patch("app.agents.planner_node.call_llm", side_effect=decisions), \
             patch("app.agents.tool_executor_node.travel_record_tool") as travel, \
             patch("app.agents.tool_executor_node.invoice_verify_tool") as inv, \
             patch("app.agents.tool_executor_node.expense_proposal_tool") as prop:
            travel.invoke.return_value = TRAVEL_ANSWER
            inv.invoke.return_value = INVOICE_ANSWER
            prop.invoke.return_value = json.dumps({
                "success": True, "kind": "proposal",
                "action_proposal": {
                    "action_type": "EXPENSE_CLAIM",
                    "trip_id": "TRIP-20260818-001",
                    "claimed_amount": "1600.00",
                    "reimbursable_amount": "1500.00",
                    "cost_center": "COST-DEFAULT",
                    "reason": "报销上周上海出差酒店",
                    "invoice_ids": ["INV-001"],
                    "expense_items": [],
                    "stay_nights": 2,
                },
                "missing_fields": [],
            }, ensure_ascii=False)
            result = run_langgraph_agent(
                "帮我报销上周上海出差的酒店和打车费用",
                use_planner=True, employee_id="E10001",
                allow_business_actions=True, business_date=date(2026, 8, 26))
        names = [h["tool_name"] for h in result["tool_history"]]
        assert names == ["travel_record_tool", "invoice_verify_tool",
                         "expense_proposal_tool"]
        # finalize contract: last proposal tool → route=action
        assert result["route"] == "action"
        assert result["category"] == "business_action"
        assert result["action_proposal"]["action_type"] == "EXPENSE_CLAIM"
        assert result["action_proposal"]["trip_id"] == "TRIP-20260818-001"

    def test_rag_success_then_invalid_finish_repairs_to_expense_proposal(self):
        """只读事实成功后错误 finish，语义修复必须继续报销 Proposal。"""
        invalid_finish = json.dumps({
            "action": "finish",
            "answer": "INVALID_FINISH_SHOULD_NOT_EXECUTE",
            "reason_code": "need_knowledge",
        }, ensure_ascii=False)
        decisions = [
            _tool("rag_answer_tool", {"question": "出差报销政策"}, "need_knowledge"),
            invalid_finish,
            _tool("expense_proposal_tool", {}, "need_expense_proposal"),
            _finish("已生成报销申请草稿，请确认后提交。"),
        ]
        expense_payload = json.dumps({
            "success": True,
            "kind": "proposal",
            "action_proposal": {
                "action_type": "EXPENSE_CLAIM",
                "trip_id": "TRIP-20260818-001",
                "claimed_amount": "1600.00",
                "reimbursable_amount": "1500.00",
                "cost_center": "COST-DEFAULT",
                "reason": "客户拜访",
                "invoice_ids": ["INV-001"],
                "expense_items": [],
                "stay_nights": 2,
            },
            "missing_fields": [],
        }, ensure_ascii=False)
        rag = Mock()
        rag.invoke.return_value = RAG_ANSWER
        proposal = Mock()
        proposal.invoke.return_value = expense_payload
        with patch("app.agents.planner_node.call_llm", side_effect=decisions) as llm, \
             patch("app.agents.tool_executor_node.rag_answer_tool", rag), \
             patch("app.agents.tool_executor_node.expense_proposal_tool", proposal):
            result = run_langgraph_agent(
                "根据最近一次已批准出差和发票，帮我准备差旅报销申请",
                use_planner=True,
                employee_id="E10001",
                allow_business_actions=True,
                business_date=date(2026, 8, 26),
            )

        assert llm.call_count == 4
        assert rag.invoke.call_count == 1
        assert proposal.invoke.call_count == 1
        assert result["stop_reason"] == "task_complete"
        assert result["route"] == "action"
        assert result["action_proposal"]["action_type"] == "EXPENSE_CLAIM"


class TestStressScenarios:
    """V2 §二十八 Stress A / C / F。"""

    def test_stress_a_travel_success_then_invoice_error_no_repeat_travel(self):
        """Stress A：travel 已成功 → invoice 失败 → 不重复执行已成功 travel。"""
        decisions = [
            _tool("travel_record_tool", {}, "need_travel_history"),
            _tool("invoice_verify_tool", {"invoice_id": "INV-001"},
                  "need_invoice_verify"),
            # Planner 再次输出 travel（错误倾向）—— 应被 dedup 阻止
            _tool("travel_record_tool", {}, "need_travel_history"),
            _finish("旅行出差记录已获取；发票验真失败，无法继续。"),
        ]
        with patch("app.agents.planner_node.call_llm", side_effect=decisions), \
             patch("app.agents.tool_executor_node.travel_record_tool") as travel, \
             patch("app.agents.tool_executor_node.invoice_verify_tool") as inv:
            travel.invoke.return_value = TRAVEL_ANSWER
            inv.invoke.side_effect = TimeoutError("invoice timeout")
            result = run_langgraph_agent(
                "帮我报销上周上海出差的酒店费用",
                use_planner=True, employee_id="E10001",
                allow_business_actions=True, business_date=date(2026, 8, 26))
        travel_calls = [h for h in result["tool_history"]
                        if h["tool_name"] == "travel_record_tool"]
        assert travel_calls[0]["status"] == "success"
        # 重复 travel 决策 → already_completed（成功签名去重）
        assert any(h["status"] == "blocked" for h in result["tool_history"])
        # travel_record_tool.invoke 只被调用一次
        assert travel.invoke.call_count == 1

    def test_stress_c_proposal_repeat_planning_blocked(self):
        """Stress C：expense_proposal 已成功 → 相同参数再规划 → already_completed。"""
        decisions = [
            _tool("travel_record_tool", {}, "need_travel_history"),
            _tool("invoice_verify_tool", {"invoice_id": "INV-001"},
                  "need_invoice_verify"),
            _tool("expense_proposal_tool", {}, "need_expense_proposal"),
            _tool("expense_proposal_tool", {}, "need_expense_proposal"),
            _finish("已生成报销申请草稿。"),
        ]
        with patch("app.agents.planner_node.call_llm", side_effect=decisions), \
             patch("app.agents.tool_executor_node.travel_record_tool") as travel, \
             patch("app.agents.tool_executor_node.invoice_verify_tool") as inv, \
             patch("app.agents.tool_executor_node.expense_proposal_tool") as prop:
            travel.invoke.return_value = TRAVEL_ANSWER
            inv.invoke.return_value = INVOICE_ANSWER
            prop.invoke.return_value = json.dumps({
                "success": True, "kind": "proposal",
                "action_proposal": {
                    "action_type": "EXPENSE_CLAIM",
                    "trip_id": "TRIP-20260818-001",
                },
                "missing_fields": [],
            }, ensure_ascii=False)
            result = run_langgraph_agent(
                "帮我报销上周上海出差的酒店和打车费用",
                use_planner=True, employee_id="E10001",
                allow_business_actions=True, business_date=date(2026, 8, 26))
        # Stress C：相同签名（expense_proposal_tool + arguments={}）已成功 →
        # 再次规划被 already_completed 阻断（success signature dedup）。
        blocked_proposal = [h for h in result["tool_history"]
                            if h["tool_name"] == "expense_proposal_tool"
                            and h["status"] == "blocked"]
        assert len(blocked_proposal) == 1
        # proposal tool 只真正执行一次
        assert prop.invoke.call_count == 1

    def test_stress_f_old_leave_memory_does_not_hijack_expense_query(self):
        """Stress F：ACTIVE LEAVE_REQUEST Memory + 报销状态问题 → 不回到 leave_proposal。"""
        decisions = [
            _tool("expense_status_tool", {"expense_id": "EXP-20260826-000001"},
                  "need_expense_status"),
            _finish("报销单 EXP-20260826-000001 状态：SUBMITTED。"),
        ]
        with patch("app.agents.planner_node.call_llm", side_effect=decisions), \
             patch("app.agents.tool_executor_node.expense_status_tool") as tk:
            tk.invoke.return_value = json.dumps({
                "success": True, "expense_id": "EXP-20260826-000001",
                "status": "SUBMITTED",
            }, ensure_ascii=False)
            result = run_langgraph_agent(
                "我那笔报销 EXP-20260826-000001 现在审批到哪了？",
                use_planner=True, employee_id="E10001",
                allow_business_actions=True, business_date=date(2026, 8, 26),
                memory_context={
                    "taskType": "LEAVE_REQUEST",
                    "status": "ACTIVE",
                    "taskStateJson": '{"start_date":"2026-07-20"}',
                    "summary": "上周提交了年假申请",
                })
        assert any(h["tool_name"] == "expense_status_tool"
                   and h["status"] == "success" for h in result["tool_history"])
        assert not any(h["tool_name"] == "leave_proposal_tool"
                       for h in result["tool_history"])
