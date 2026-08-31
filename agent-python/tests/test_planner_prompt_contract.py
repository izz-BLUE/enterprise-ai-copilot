"""Planner Prompt 与 PlannerDecision Schema 契约一致性测试。

Planner system prompt 中声明的字段名、枚举值与 JSON 示例必须与
app.schemas.planner_schema.PlannerDecision 保持完全一致：
- Schema 新增/修改字段或枚举值而 Prompt 未同步 → 本文件测试失败；
- 动态 Prompt 中的合法示例必须是 Schema 可校验通过的决策；
- 静态 Prompt 不得提前暴露当前请求不可见的 Tool。
"""

import json

import pytest

from app.agents.planner_node import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_prompt,
    build_planner_system_prompt,
)
from app.schemas.planner_schema import (
    Action,
    PlannerDecision,
    ReasonCode,
    ToolName,
)

# 必须与 PLANNER_SYSTEM_PROMPT 中的示例逐字一致（含键顺序与空格）
_LEGAL_EXAMPLES = [
    {"action": "tool", "tool_name": "rag_answer_tool",
     "arguments": {"question": "公司的年假制度是什么"}, "reason_code": "need_knowledge",
     "expense_reason": None},
    {"action": "tool", "tool_name": "eval_report_tool",
     "arguments": {"report_type": "all"}, "reason_code": "need_eval",
     "expense_reason": None},
    {"action": "tool", "tool_name": "leave_balance_tool",
     "arguments": {}, "reason_code": "need_balance", "expense_reason": None},
    {"action": "tool", "tool_name": "leave_request_tool",
     "arguments": {"limit": 10}, "reason_code": "need_leave_history",
     "expense_reason": None},
    {"action": "finish", "answer": "年假制度：入职满1年5天。",
     "reason_code": "task_complete", "expense_reason": None},
    {"action": "refuse", "answer": "该请求不允许处理。",
     "reason_code": "not_allowed", "expense_reason": None},
    {"action": "tool", "tool_name": "leave_proposal_tool",
     "arguments": {}, "reason_code": "need_proposal", "expense_reason": None},
]

_FIELD_NAMES = ('action', 'tool_name', 'arguments', 'answer', 'reason_code', 'expense_reason')
_FORBIDDEN_FIELDS = ('decision', 'call_tool', 'thought', 'reasoning', 'plan')


def test_prompt_declares_all_schema_fields():
    for field in _FIELD_NAMES:
        assert field in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_all_action_values():
    for value in Action.__args__:
        assert f'"{value}"' in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_all_tool_names():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    for value in ToolName.__args__:
        assert value in prompt


def test_prompt_declares_all_reason_codes():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    for value in ReasonCode.__args__:
        assert f'"{value}"' in prompt


def test_prompt_requires_completion_check_before_tool_call():
    """Prompt 必须要求先检查任务完成度，再决定是否继续调用 Tool。"""
    assert '先检查 tool_history' in PLANNER_SYSTEM_PROMPT
    assert '优先选择 finish' in PLANNER_SYSTEM_PROMPT
    assert '已成功获得' in PLANNER_SYSTEM_PROMPT
    assert '重复执行已经成功完成的相同调用' in PLANNER_SYSTEM_PROMPT
    assert '只读查询或知识检索成功只表示事实已获得' in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_expense_reason_semantic_boundary():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    assert 'expense_reason' in prompt
    assert '不得使用出差记录的 purpose' in prompt
    assert 'Tool History、execution_history 或 Memory Context' in prompt
    assert '孤立的一句“客户拜访”' in prompt
    assert 'expense_reason 不属于 arguments' in prompt
    assert 'arguments 仍为 {}' in prompt


def test_prompt_declares_expense_reason_first_and_invoice_prerequisite():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    assert 'expense_reason 缺失时应优先调用该 Tool' in prompt
    assert '所有需要的发票验真成功后才能调用 expense_proposal_tool' in prompt
    assert '不得跳过验真直接生成草稿' in prompt


def test_prompt_scopes_invoice_verification_to_selected_trip_only():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    assert '每个 trip 的 expense_documents 只属于该 trip，不能跨 trip 合并' in prompt
    assert 'invoice_verify_tool 的范围严格等于 selected trip 的 expense_documents' in prompt
    assert '不得验证其它 trip 的 invoice references' in prompt
    assert 'selected trip 的 expense_documents 全部成功验真后，必须立即调用 expense_proposal_tool' in prompt
    assert 'selected trip 没有 expense_documents 时不得借用其它 trip 的发票' in prompt


_MULTI_TRIP_OBSERVATION = json.dumps({
    'success': True,
    'items': [
        {
            'trip_id': 'TRIP-20260818-001',
            'status': 'APPROVED',
            'expense_documents': [
                {'invoice_id': 'INV-001'},
                {'invoice_id': 'INV-002'},
            ],
        },
        {
            'trip_id': 'TRIP-20260825-003',
            'status': 'PENDING',
            'expense_documents': [{'invoice_id': 'INV-006'}],
        },
    ],
}, ensure_ascii=False)


@pytest.mark.parametrize(
    ('case_name', 'question', 'tool_history', 'observation', 'memory_context',
     'continuation_original_request'),
    [
        ('case1', '最近一次已批准出差和对应发票', [], _MULTI_TRIP_OBSERVATION,
         None, None),
        ('case2', '最近一次已批准出差和对应发票', [
            {'tool_name': 'travel_record_tool', 'status': 'success',
             'arguments': {}, 'observation': _MULTI_TRIP_OBSERVATION},
            {'tool_name': 'invoice_verify_tool', 'status': 'success',
             'arguments': {'invoice_id': 'INV-001'},
             'observation': '{"success":true,"invoice_id":"INV-001"}'},
        ], '{"success":true,"invoice_id":"INV-001"}', None, None),
        ('case3', '最近一次已批准出差和对应发票', [
            {'tool_name': 'travel_record_tool', 'status': 'success',
             'arguments': {}, 'observation': _MULTI_TRIP_OBSERVATION},
            {'tool_name': 'invoice_verify_tool', 'status': 'success',
             'arguments': {'invoice_id': 'INV-001'},
             'observation': '{"success":true,"invoice_id":"INV-001"}'},
            {'tool_name': 'invoice_verify_tool', 'status': 'success',
             'arguments': {'invoice_id': 'INV-002'},
             'observation': '{"success":true,"invoice_id":"INV-002"}'},
        ], '{"success":true,"invoice_id":"INV-002"}', None, None),
        ('case4', '目标 trip 没有发票时准备报销', [], json.dumps({
            'success': True,
            'items': [
                {'trip_id': 'TRIP-TARGET', 'status': 'APPROVED',
                 'expense_documents': []},
                {'trip_id': 'TRIP-OTHER', 'status': 'APPROVED',
                 'expense_documents': [{'invoice_id': 'INV-OTHER'}]},
            ],
        }, ensure_ascii=False), None, None),
        ('case5', '请报销 TRIP-20260818-001 的对应发票', [],
         _MULTI_TRIP_OBSERVATION, None, None),
        ('case6', '客户拜访', [], _MULTI_TRIP_OBSERVATION, {
            'taskType': 'EXPENSE_REQUEST',
            'status': 'ACTIVE',
            'taskStateJson': '{"waiting_for":"reason"}',
        }, '根据我最近一次已批准的出差和对应发票，帮我准备差旅报销申请。'),
    ],
)
def test_expense_planner_cases_receive_scoped_trip_invoice_contract(
    case_name, question, tool_history, observation, memory_context,
    continuation_original_request,
):
    """所有 Expense Planner 场景都收到同一 selected-trip scope contract。"""
    del case_name
    tools = ['travel_record_tool', 'invoice_verify_tool', 'expense_proposal_tool']
    system = build_planner_system_prompt(tools)
    user = build_planner_prompt(
        question,
        tools,
        tool_history,
        observation,
        3,
        memory_context,
        continuation_original_request=continuation_original_request,
    )
    assert 'invoice_verify_tool 的范围严格等于 selected trip 的 expense_documents' in system
    assert '不得验证其它 trip 的 invoice references' in system
    assert 'selected trip 的 expense_documents 全部成功验真后，必须立即调用 expense_proposal_tool' in system
    assert 'selected trip 没有 expense_documents 时不得借用其它 trip 的发票' in system
    assert observation in user
    if continuation_original_request:
        assert 'current user input（expense_reason 来源）: 客户拜访' in user
        assert continuation_original_request in user


def test_dynamic_prompt_requires_business_proposal_after_read_only_facts():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    assert 'leave_balance_tool 成功只表示余额已查询' in prompt
    assert '应继续调用 leave_proposal_tool' in prompt
    assert 'rag_answer_tool、travel_record_tool、invoice_verify_tool 成功只提供报销所需事实' in prompt
    assert '应继续调用 expense_proposal_tool' in prompt
    assert '只生成待确认草稿，不执行业务写操作' in prompt


def test_prompt_forbids_undefined_fields():
    for field in _FORBIDDEN_FIELDS:
        assert field in PLANNER_SYSTEM_PROMPT


def test_each_prompt_example_validates_against_schema():
    for example in _LEGAL_EXAMPLES:
        decision = PlannerDecision.model_validate(example)
        decision.validate_decision()


def test_examples_appear_verbatim_in_prompt():
    prompt = build_planner_system_prompt(list(ToolName.__args__))
    for example in _LEGAL_EXAMPLES:
        assert json.dumps(example, ensure_ascii=False) in prompt


def test_static_prompt_does_not_expose_tool_names():
    for value in ToolName.__args__:
        assert value not in PLANNER_SYSTEM_PROMPT
