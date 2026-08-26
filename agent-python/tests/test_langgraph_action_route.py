from datetime import date
from unittest.mock import Mock, patch

from app.agents.langgraph_agent import action_node as _action_node
from app.agents.langgraph_agent import router_node as _router_node
from app.agents.langgraph_agent import run_langgraph_agent
from app.schemas.action_schema import (
    AnnualLeaveActionProposal,
    AnnualLeaveClarification,
    ClarificationPlanningResult,
    ProposalPlanningResult,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

BUSINESS_DATE = date(2026, 7, 16)


def state(question, **changes):
    value = {
        "question": question,
        "safe": True,
        "route": "",
        "answer": "",
        "tool_result": {},
        "sources": [],
        "reason": "",
        "category": "",
        "allow_eval": False,
        "allow_business_actions": True,
        "business_date": BUSINESS_DATE,
        "trace_id": "trace",
        "action_proposal": None,
        "missing_fields": [],
    }
    value.update(changes)
    return value


def router_node(value, runtime=None):
    if runtime is None:
        runtime = runtime_for_state(value)
        value = checkpoint_safe_state(value)
    return _router_node(value, runtime)


def action_node(value, runtime=None):
    if runtime is None:
        runtime = runtime_for_state(value)
        value = checkpoint_safe_state(value)
    return _action_node(value, runtime)


def test_router_order_preserves_safety_and_eval_permissions():
    assert router_node(state("申请年假", safe=False))["route"] == "refuse"
    assert router_node(state("查看评估通过率", allow_eval=True))["route"] == "eval"
    denied = router_node(state("查看评估通过率", allow_eval=False))
    assert denied["route"] == "refuse"
    assert denied["category"] == "access_control"


def test_policy_and_normal_questions_remain_rag():
    assert router_node(state("公司的年假政策是什么"))["route"] == "rag"
    assert router_node(state("公司的报销流程是什么"))["route"] == "rag"


def test_action_requires_permission_and_java_business_date():
    denied = router_node(state(
        "申请2026-07-20一天年假，原因为私事",
        allow_business_actions=False,
    ))
    unavailable = router_node(state(
        "申请2026-07-20一天年假，原因为私事",
        business_date=None,
    ))
    assert denied["route"] == "refuse"
    assert denied["category"] == "access_control"
    assert unavailable["route"] == "refuse"
    assert unavailable["category"] == "business_action"


def test_missing_date_and_reason_return_action_clarification_without_provider():
    for question, expected in (
        ("申请一天年假，原因为私事", ["start_date", "end_date"]),
        ("申请2026-07-20一天年假", ["reason"]),
    ):
        with patch(
            "app.services.tool_calling_service._get_controlled_tool_client",
            side_effect=AssertionError("provider must not be called"),
        ):
            result = action_node(state(question))
        assert result["route"] == "action"
        assert result["missing_fields"] == expected
        assert result["action_proposal"] is None


def test_complete_action_returns_public_safe_proposal_shape():
    proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
        action_type="ANNUAL_LEAVE_REQUEST",
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        reason="私事",
        half_day="NONE",
    ))
    with patch(
        "app.agents.langgraph_agent.plan_annual_leave_action",
        return_value=proposal,
    ):
        result = action_node(state("申请2026-07-20一天年假，原因为私事"))
    assert result["route"] == "action"
    assert result["category"] == "business_action"
    assert result["missing_fields"] == []
    assert result["action_proposal"]["action_type"] == "ANNUAL_LEAVE_REQUEST"
    serialized = str(result["action_proposal"])
    for forbidden in ("actionId", "nonce", "employeeId"):
        assert forbidden not in serialized


def test_action_path_does_not_call_rag_tool():
    clarification = ClarificationPlanningResult(
        clarification=AnnualLeaveClarification(
            missing_fields=["reason"], question="请补充年假申请原因。"
        )
    )
    rag = Mock()
    with patch(
        "app.agents.langgraph_agent.plan_annual_leave_action",
        return_value=clarification,
    ) as planner, patch("app.agents.langgraph_agent.rag_answer_tool", rag):
        result = run_langgraph_agent(
            "申请2026-07-20一天年假",
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
        )
    assert result["route"] == "action"
    planner.assert_called_once()
    rag.invoke.assert_not_called()


def test_rag_path_does_not_call_action_planner():
    rag_payload = '{"answer":"policy","success":true,"sources":[]}'
    rag = Mock()
    rag.invoke.return_value = rag_payload
    with patch(
        "app.agents.langgraph_agent.rewrite_query",
        return_value={
            "rewritten_query": "年假政策",
            "rewrite_applied": False,
            "rewrite_reason": "",
        },
    ), patch(
        "app.agents.langgraph_agent.rag_answer_tool", rag
    ), patch(
        "app.agents.langgraph_agent.plan_annual_leave_action"
    ) as planner:
        result = run_langgraph_agent(
            "公司的年假政策是什么",
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
        )
    assert result["route"] == "rag"
    rag.invoke.assert_called_once()
    planner.assert_not_called()


def test_unsafe_request_still_refuses_before_action():
    with patch(
        "app.agents.langgraph_agent.check_user_query_safety",
        return_value={
            "safe": False,
            "category": "policy_bypass",
            "reason": "blocked",
            "message": "拒绝",
        },
    ), patch("app.agents.langgraph_agent.plan_annual_leave_action") as planner:
        result = run_langgraph_agent(
            "绕过审批申请年假",
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
        )
    assert result["route"] == "refuse"
    assert result["answer"] == "拒绝"
    planner.assert_not_called()
