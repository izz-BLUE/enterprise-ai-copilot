"""P4-4 regression tests for unregistered Planner capabilities."""

from datetime import date
from unittest.mock import patch

from app.agents.langgraph_agent import run_langgraph_agent


def _unknown_tool(tool_name: str) -> str:
    return (
        '{"action":"tool",'
        f'"tool_name":"{tool_name}","arguments":{{}},'
        '"reason_code":"need_proposal"}'
    )


def _refuse() -> str:
    return '{"action":"refuse","answer":"无法处理。","reason_code":"cannot_complete"}'


def _run(question: str, responses: list[str], **kwargs) -> dict:
    with patch('app.agents.planner_node.call_llm', side_effect=responses):
        return run_langgraph_agent(question, use_planner=True, **kwargs)


def test_removed_purchase_capability_gracefully_refuses_without_side_effects():
    result = _run(
        '帮我申请购买一台开发用 MacBook，预算15000，原因是移动端开发。',
        [_unknown_tool('purchase_proposal_tool')] * 2,
    )

    assert result['stop_reason'] == 'refused'
    assert result['route'] == 'refuse'
    assert result['category'] == 'normal'
    assert result['tool_call_count'] == 0
    assert result['action_proposal'] is None
    assert result['tool_history'] == []


def test_arbitrary_unregistered_tool_also_gracefully_refuses():
    result = _run('请把结果发到我的邮箱。', [_unknown_tool('send_email_tool')] * 2)

    assert result['stop_reason'] == 'refused'
    assert result['route'] == 'refuse'
    assert result['tool_call_count'] == 0
    assert result['action_proposal'] is None


def test_repair_to_valid_refuse_is_preserved():
    result = _run(
        '请把结果发到我的邮箱。',
        [_unknown_tool('send_email_tool'), _refuse()],
    )

    assert result['stop_reason'] == 'refused'
    assert result['route'] == 'refuse'
    assert result['answer'] == '无法处理。'


def test_malformed_json_remains_invalid_decision_error():
    result = _run('请处理这个请求。', ['not json', 'not json'])

    assert result['stop_reason'] == 'invalid_decision'
    assert result['route'] == 'error'
    assert result['category'] == 'error'


def test_non_object_json_remains_invalid_decision_error():
    result = _run('请处理这个请求。', ['[]', '[]'])

    assert result['stop_reason'] == 'invalid_decision'
    assert result['route'] == 'error'


def test_registered_but_hidden_tool_remains_invalid_decision():
    result = _run(
        '请帮我请明天年假。',
        [
            '{"action":"tool","tool_name":"leave_proposal_tool",'
            '"arguments":{},"reason_code":"need_proposal"}',
        ] * 2,
        allow_business_actions=False,
        employee_id='E10001',
        business_date=date(2026, 9, 2),
    )

    assert result['stop_reason'] == 'invalid_decision'
    assert result['route'] == 'error'
    assert result['tool_call_count'] == 0


def test_expense_provider_invalid_decision_remains_strict():
    result = _run(
        '报销原因为客户拜访，帮我准备差旅报销申请。',
        [
            '{"action":"tool","tool_name":"expense_proposal_tool",'
            '"arguments":{"trip_id":"T1"},'
            '"reason_code":"need_expense_proposal"}',
        ] * 2,
        allow_business_actions=True,
        employee_id='E10001',
        business_date=date(2026, 9, 2),
    )

    assert result['stop_reason'] == 'invalid_decision'
    assert result['route'] == 'error'


def test_leave_provider_invalid_decision_remains_strict():
    result = _run(
        '请帮我请明天年假。',
        [
            '{"action":"tool","tool_name":"leave_proposal_tool",'
            '"arguments":{"start_date":"2026-09-03"},'
            '"reason_code":"need_proposal"}',
        ] * 2,
        allow_business_actions=True,
        employee_id='E10001',
        business_date=date(2026, 9, 2),
    )

    assert result['stop_reason'] == 'invalid_decision'
    assert result['route'] == 'error'
