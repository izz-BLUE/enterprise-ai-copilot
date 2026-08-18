"""Planner Prompt 与 PlannerDecision Schema 契约一致性测试。

PLANNER_SYSTEM_PROMPT 中声明的字段名、枚举值与 JSON 示例必须与
app.schemas.planner_schema.PlannerDecision 保持完全一致：
- Schema 新增/修改字段或枚举值而 Prompt 未同步 → 本文件测试失败；
- Prompt 中的合法示例必须是 Schema 可校验通过的决策。
"""

import json

from app.agents.planner_node import PLANNER_SYSTEM_PROMPT
from app.schemas.planner_schema import (
    Action,
    PlannerDecision,
    ReasonCode,
    ToolName,
)

# 必须与 PLANNER_SYSTEM_PROMPT 中的示例逐字一致（含键顺序与空格）
_LEGAL_EXAMPLES = [
    {"action": "tool", "tool_name": "rag_answer_tool",
     "arguments": {"question": "公司的年假制度是什么"}, "reason_code": "need_knowledge"},
    {"action": "tool", "tool_name": "eval_report_tool",
     "arguments": {"report_type": "all"}, "reason_code": "need_eval"},
    {"action": "tool", "tool_name": "leave_balance_tool",
     "arguments": {}, "reason_code": "need_balance"},
    {"action": "tool", "tool_name": "leave_request_tool",
     "arguments": {"limit": 10}, "reason_code": "need_leave_history"},
    {"action": "finish", "answer": "年假制度：入职满1年5天。",
     "reason_code": "task_complete"},
    {"action": "refuse", "answer": "该请求不允许处理。",
     "reason_code": "not_allowed"},
    {"action": "tool", "tool_name": "leave_proposal_tool",
     "arguments": {}, "reason_code": "need_proposal"},
]

_FIELD_NAMES = ('action', 'tool_name', 'arguments', 'answer', 'reason_code')
_FORBIDDEN_FIELDS = ('decision', 'call_tool', 'thought', 'reasoning', 'plan')


def test_prompt_declares_all_schema_fields():
    for field in _FIELD_NAMES:
        assert field in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_all_action_values():
    for value in Action.__args__:
        assert f'"{value}"' in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_all_tool_names():
    for value in ToolName.__args__:
        assert value in PLANNER_SYSTEM_PROMPT


def test_prompt_declares_all_reason_codes():
    for value in ReasonCode.__args__:
        assert f'"{value}"' in PLANNER_SYSTEM_PROMPT


def test_prompt_requires_completion_check_before_tool_call():
    """Prompt 必须要求先检查任务完成度，再决定是否继续调用 Tool。"""
    assert '先检查 tool_history' in PLANNER_SYSTEM_PROMPT
    assert '优先选择 finish' in PLANNER_SYSTEM_PROMPT
    assert '已成功获得' in PLANNER_SYSTEM_PROMPT
    assert '重复执行已经成功完成的相同调用' in PLANNER_SYSTEM_PROMPT


def test_prompt_forbids_undefined_fields():
    for field in _FORBIDDEN_FIELDS:
        assert field in PLANNER_SYSTEM_PROMPT


def test_each_prompt_example_validates_against_schema():
    for example in _LEGAL_EXAMPLES:
        decision = PlannerDecision.model_validate(example)
        decision.validate_decision()


def test_examples_appear_verbatim_in_prompt():
    for example in _LEGAL_EXAMPLES:
        assert json.dumps(example, ensure_ascii=False) in PLANNER_SYSTEM_PROMPT
