"""Phase D1 routing-evaluation and Planner-first regression tests."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app.agents import planner_node as planner_module
from app.agents.langgraph_agent import run_langgraph_agent
from app.schemas.planner_schema import (
    EXPENSE_STATUS_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    RAG_TOOL_NAME,
)
from evals.routing.run_routing_eval import (
    DATASET_PATH,
    EVAL_EMPLOYEE_ID,
    FAILURE_TYPES,
    _parse_decision,
    authorized_tools_for_case,
    build_routing_prompts,
    evaluate_cases,
    load_cases,
    score_decision,
)


def _tool(tool_name: str, arguments: dict, reason_code: str) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': arguments,
        'reason_code': reason_code,
    })


def _finish(answer: str) -> str:
    return json.dumps({
        'action': 'finish',
        'answer': answer,
        'reason_code': 'task_complete',
    })


def test_routing_corpus_meets_phase_d1_coverage_contract():
    cases = load_cases(DATASET_PATH)
    counts = {category: sum(case['category'] == category for case in cases) for category in {
        'knowledge_rag', 'leave_live_read', 'leave_proposal', 'expense_knowledge',
        'expense_live_read', 'expense_proposal', 'cross_domain',
        'negative_unsupported', 'permission_boundary',
    }}

    assert len(cases) == 130
    assert counts == {
        'knowledge_rag': 15,
        'leave_live_read': 18,
        'leave_proposal': 15,
        'expense_knowledge': 12,
        'expense_live_read': 18,
        'expense_proposal': 15,
        'cross_domain': 10,
        'negative_unsupported': 12,
        'permission_boundary': 15,
    }
    assert all(set(case) >= {
        'id', 'category', 'question', 'runtime_profile', 'expected',
    } for case in cases)


def test_formal_candidate_set_is_profile_based_not_question_based():
    cases = load_cases(DATASET_PATH)
    full_cases = [case for case in cases if case['runtime_profile'] == 'EMPLOYEE_FULL']
    knowledge_case = next(case for case in full_cases if case['category'] == 'knowledge_rag')
    expense_case = next(case for case in full_cases if case['category'] == 'expense_live_read')

    knowledge_tools, knowledge_system, knowledge_user = build_routing_prompts(knowledge_case)
    expense_tools, expense_system, expense_user = build_routing_prompts(expense_case)

    assert knowledge_tools == expense_tools == authorized_tools_for_case(knowledge_case)
    assert knowledge_system == expense_system
    assert knowledge_user != expense_user
    assert RAG_TOOL_NAME in knowledge_system
    assert EVAL_EMPLOYEE_ID not in knowledge_system + knowledge_user


def test_routing_evaluator_stops_at_schema_without_tool_or_state_side_effects():
    case = next(case for case in load_cases(DATASET_PATH) if case['id'] == 'd1-b-001')
    prompts: list[tuple[str, str]] = []

    def fake_llm(system_prompt: str, user_prompt: str, **_options):
        prompts.append((system_prompt, user_prompt))
        return _tool(LEAVE_BALANCE_TOOL_NAME, {}, 'need_balance')

    report = evaluate_cases(
        [case],
        runs=2,
        llm_call_factory=lambda _case: fake_llm,
    )

    assert len(prompts) == 2
    assert report['metrics']['overall_tool_selection_accuracy']['rate'] == 1.0
    assert report['metrics']['schema_valid_rate']['rate'] == 1.0
    assert set(report['metrics']['failure_type_counts']) == set(FAILURE_TYPES)
    assert report['metrics']['perfect_stability']['buckets'] == {'2/2': 1, '1/2': 0, '0/2': 0}
    assert report['safety'] == {
        'raw_prompts_saved': False,
        'raw_llm_responses_saved': False,
        'real_identity_or_token_used': False,
        'tools_executed': False,
        'state_persisted': False,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert EVAL_EMPLOYEE_ID not in serialized
    assert 'd1-eval-internal-token' not in serialized


def test_invalid_and_unauthorized_decisions_are_distinguished():
    case = next(case for case in load_cases(DATASET_PATH) if case['id'] == 'd1-i-001')
    authorized = authorized_tools_for_case(case)
    hidden_payload = {
        'action': 'tool',
        'tool_name': 'leave_proposal_tool',
        'arguments': {},
        'reason_code': 'need_proposal',
    }
    hidden = score_decision(case, _parse_decision(json.dumps(hidden_payload)), authorized)
    unknown = _parse_decision(json.dumps({
        **hidden_payload,
        'tool_name': 'not_registered_tool',
    }))

    assert hidden['schema_valid'] is True
    assert hidden['failure_type'] == 'UNAUTHORIZED_SELECTION'
    assert hidden['passed'] is False
    assert unknown['schema_valid'] is False
    assert unknown['failure_type'] == 'UNKNOWN_TOOL'


def test_routing_runner_has_no_executor_or_legacy_resolution_dependency():
    source = Path(__file__).parents[1].joinpath(
        'evals', 'routing', 'run_routing_eval.py',
    ).read_text(encoding='utf-8')

    assert 'tool_executor_node' not in source
    assert 'run_langgraph_agent' not in source
    assert 'matches(' not in source
    assert 'DOMAIN_PROVIDER_REGISTRY.resolve' not in source


def test_phase_d1_expense_chain_regression_keeps_two_read_tools_and_finishes():
    decisions = [
        _tool(LEAVE_BALANCE_TOOL_NAME, {}, 'need_balance'),
        _tool(EXPENSE_STATUS_TOOL_NAME, {}, 'need_expense_status'),
        _finish('余额和报销状态已查询。'),
    ]
    balance_tool = Mock()
    balance_tool.invoke.return_value = json.dumps({
        'success': True,
        'data': {'remaining_days': 5},
    })
    status_tool = Mock()
    status_tool.invoke.return_value = json.dumps({
        'success': True,
        'items': [{'request_id': 'REQ-D1', 'status': 'SUCCEEDED'}],
    })

    with patch.object(planner_module, 'JAVA_BASE_URL', 'http://java'), \
            patch.object(planner_module, 'JAVA_INTERNAL_TOKEN', 'internal'), \
            patch.object(planner_module, 'PLANNER_SHADOW_ROUTING_ENABLED', False), \
            patch.object(
                planner_module.DOMAIN_PROVIDER_REGISTRY, 'resolve',
                side_effect=AssertionError('D1 regression must not resolve by question'),
            ), \
            patch('app.agents.planner_node.call_llm', side_effect=decisions), \
            patch('app.agents.tool_executor_node.leave_balance_tool', balance_tool), \
            patch('app.agents.tool_executor_node.expense_status_tool', status_tool):
        result = run_langgraph_agent(
            '同时查询我的年假余额和最近报销状态',
            allow_business_actions=False,
            business_date=date(2026, 9, 4),
            employee_id='E10001',
            use_planner=True,
        )

    assert [item['tool_name'] for item in result['tool_history']] == [
        LEAVE_BALANCE_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    ]
    assert result['stop_reason'] == 'task_complete'
    assert result.get('category') not in {'ambiguity', 'refused'}
    balance_tool.invoke.assert_called_once()
    status_tool.invoke.assert_called_once()
