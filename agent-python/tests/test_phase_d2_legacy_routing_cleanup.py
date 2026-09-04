"""Phase D2 architecture regressions for retired semantic routing cleanup."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.agents import planner_node as planner_module
from app.agents.tool_catalog import TOOL_CATALOG
from app.schemas.planner_schema import (
    EXPENSE_STATUS_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    RAG_TOOL_NAME,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state


def _state(question: str) -> dict:
    return {
        'question': question,
        'safe': True,
        'route': '',
        'answer': '',
        'tool_result': {},
        'sources': [],
        'reason': '',
        'category': '',
        'action_proposal': None,
        'missing_fields': [],
        'request_expense_reason': None,
        'step_count': 0,
        'tool_call_count': 0,
        'tool_history': [],
        'observation': '',
        'memory_context': None,
        'execution_history': [],
        'continuation_leave_state': None,
        'planner_decision': None,
        'stop_reason': '',
        'employee_id': 'E10001',
        'allow_eval': False,
        'allow_business_actions': True,
        'business_date': date(2026, 9, 4),
        'trace_id': 'phase-d2-routing',
    }


def _tool(tool_name: str, arguments: dict, reason_code: str) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
    }, ensure_ascii=False)


def test_same_runtime_context_keeps_candidate_set_and_status_question_independent(
    monkeypatch,
):
    monkeypatch.setenv('ENTERPRISE_OA_MCP_URL', 'http://oa-mcp')
    monkeypatch.setattr(planner_module, 'JAVA_BASE_URL', 'http://java')
    monkeypatch.setattr(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal')

    questions = (
        '公司的年假制度是什么',
        '查询我的年假余额',
        '查询我的报销状态',
    )
    candidate_sets: list[list[str]] = []
    system_prompts: list[str] = []
    real_authorized_tools = planner_module.authorized_tools

    def capture_authorized_tools(**kwargs):
        tools = real_authorized_tools(**kwargs)
        candidate_sets.append(tools)
        return tools

    responses = [
        _tool(RAG_TOOL_NAME, {'question': questions[0]}, 'need_knowledge'),
        _tool(LEAVE_BALANCE_TOOL_NAME, {}, 'need_balance'),
        _tool(EXPENSE_STATUS_TOOL_NAME, {}, 'need_expense_status'),
    ]
    with patch.object(
        planner_module, 'authorized_tools', side_effect=capture_authorized_tools
    ), patch.object(planner_module, 'call_llm', side_effect=responses) as llm:
        decisions = [
            planner_module.planner_node(
                checkpoint_safe_state(_state(question)),
                runtime_for_state(_state(question)),
            )['planner_decision']
            for question in questions
        ]
        system_prompts = [call.args[0] for call in llm.call_args_list]

    assert candidate_sets[0] == candidate_sets[1] == candidate_sets[2]
    expected_status = TOOL_CATALOG.capability_status(candidate_sets[0])
    assert all(TOOL_CATALOG.capability_status(tools) == expected_status
               for tools in candidate_sets)
    assert system_prompts[0] == system_prompts[1] == system_prompts[2]
    assert [decision['tool_name'] for decision in decisions] == [
        RAG_TOOL_NAME,
        LEAVE_BALANCE_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    ]


def test_first_step_uses_only_formal_planner_call_after_shadow_cleanup():
    planner_source = Path(planner_module.__file__).read_text(encoding='utf-8')
    shadow_module = Path(planner_module.__file__).with_name('planner_shadow_routing.py')
    assert 'planner_shadow_routing' not in planner_source
    assert not shadow_module.exists()

    with patch.object(
        planner_module,
        'call_llm',
        return_value=_tool(RAG_TOOL_NAME, {'question': '公司的年假制度是什么'}, 'need_knowledge'),
    ) as formal_llm:
        result = planner_module.planner_node(
            checkpoint_safe_state(_state('公司的年假制度是什么')),
            runtime_for_state(_state('公司的年假制度是什么')),
        )

    formal_llm.assert_called_once()
    assert result['planner_decision']['tool_name'] == RAG_TOOL_NAME


def test_production_planner_and_registry_have_no_retired_question_router_symbols():
    planner_source = Path(planner_module.__file__).read_text(encoding='utf-8')
    registry_source = (
        Path(__file__).parents[1] / 'app' / 'agents'
        / 'domain_provider_registry.py'
    ).read_text(encoding='utf-8')

    retired_symbols = (
        'visible_tools',
        'capability_tools_for_question',
        'DomainProviderAmbiguityError',
        'is_business_action_intent',
    )
    assert all(symbol not in planner_source for symbol in retired_symbols)
    assert all(symbol not in registry_source for symbol in retired_symbols)
    assert 'def resolve(' not in registry_source
    assert 'def matches(' not in registry_source
