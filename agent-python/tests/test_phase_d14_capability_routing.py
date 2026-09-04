"""Phase D1.4 capability-aware Planner routing contract tests."""

import json

import pytest

from app.agents.planner_node import build_planner_system_prompt
from app.agents.tool_catalog import TOOL_CATALOG
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
)
from evals.routing.run_routing_eval import (
    RUNTIME_PROFILES,
    authorized_tools_for_case,
    evaluate_cases,
    load_cases,
)


def _case(
    case_id: str,
    question: str,
    profile: str,
    action: str,
    tool_names: list[str] | None = None,
) -> dict:
    return {
        'id': case_id,
        'category': 'permission_boundary',
        'question': question,
        'runtime_profile': profile,
        'expected': {
            'action': action,
            'tool_names': tool_names or [],
        },
    }


def _expected_llm(case: dict) -> str:
    expected = case['expected']
    if expected['action'] == 'tool':
        tool_name = expected['tool_names'][0]
        arguments = {'question': case['question']} if tool_name == RAG_TOOL_NAME else {}
        payload = {
            'action': 'tool',
            'tool_name': tool_name,
            'arguments': arguments,
            'reason_code': TOOL_CATALOG.prompt_spec(tool_name).reason_code,
        }
    else:
        payload = {
            'action': expected['action'],
            'answer': '评估用响应。',
            'reason_code': 'not_allowed' if expected['action'] == 'refuse' else 'task_complete',
        }
    return json.dumps(payload, ensure_ascii=False)


def _evaluate_contract_case(case: dict) -> dict:
    return evaluate_cases(
        [case],
        runs=1,
        llm_call_factory=lambda _case: (
            lambda _system_prompt, _user_prompt, **_options: _expected_llm(case)
        ),
    )


def test_capability_status_is_deterministic_summary_of_authorized_tools():
    expected = {
        'AUTHENTICATED_WITHOUT_EMPLOYEE_ID': {
            'enterprise_knowledge': 'available',
            'personal_realtime_data': 'unavailable',
            'business_action': 'unavailable',
            'eval': 'available',
        },
        'EMPLOYEE_READ_ONLY': {
            'enterprise_knowledge': 'available',
            'personal_realtime_data': 'available',
            'business_action': 'unavailable',
            'eval': 'unavailable',
        },
        'EMPLOYEE_FULL': {
            'enterprise_knowledge': 'available',
            'personal_realtime_data': 'available',
            'business_action': 'available',
            'eval': 'unavailable',
        },
    }

    for profile_name, expected_status in expected.items():
        tools = authorized_tools_for_case({'runtime_profile': profile_name})
        assert TOOL_CATALOG.capability_status(tools) == expected_status

    assert authorized_tools_for_case({'runtime_profile': 'AUTHENTICATED_WITHOUT_EMPLOYEE_ID'}) == [
        RAG_TOOL_NAME,
        'eval_report_tool',
    ]
    assert RUNTIME_PROFILES['AUTHENTICATED_WITHOUT_EMPLOYEE_ID']['employee_id'] == ''


def test_tool_catalog_declares_the_capability_category_for_each_tool():
    assert {
        name: TOOL_CATALOG.prompt_spec(name).capability_category
        for name in TOOL_CATALOG.tool_names
    } == {
        RAG_TOOL_NAME: 'enterprise_knowledge',
        EVAL_TOOL_NAME: 'eval',
        LEAVE_BALANCE_TOOL_NAME: 'personal_realtime_data',
        LEAVE_REQUEST_TOOL_NAME: 'personal_realtime_data',
        LEAVE_PROPOSAL_TOOL_NAME: 'business_action',
        TRAVEL_RECORD_TOOL_NAME: 'personal_realtime_data',
        INVOICE_VERIFY_TOOL_NAME: 'personal_realtime_data',
        EXPENSE_PROPOSAL_TOOL_NAME: 'business_action',
        EXPENSE_STATUS_TOOL_NAME: 'personal_realtime_data',
    }


def test_planner_receives_structured_capability_status_without_hidden_tools():
    tools = authorized_tools_for_case({
        'runtime_profile': 'AUTHENTICATED_WITHOUT_EMPLOYEE_ID',
    })
    prompt = build_planner_system_prompt(tools)

    assert (
        '{"enterprise_knowledge":"available","personal_realtime_data":"unavailable",'
        '"business_action":"unavailable","eval":"available"}'
    ) in prompt
    assert '用户最终目标明确依赖 unavailable capability 时必须 action=refuse' in prompt
    assert 'enterprise_knowledge 不能替代 personal_realtime_data' in prompt
    assert 'read-only prerequisite 不能替代 unavailable business_action' in prompt
    assert LEAVE_BALANCE_TOOL_NAME not in prompt


@pytest.mark.parametrize(
    ('case',),
    [
        (_case(
            'contract-admin-personal-read', '查询我的年假余额',
            'AUTHENTICATED_WITHOUT_EMPLOYEE_ID', 'refuse',
        ),),
        (_case(
            'contract-admin-knowledge', '公司的年假制度是什么？',
            'AUTHENTICATED_WITHOUT_EMPLOYEE_ID', 'tool', [RAG_TOOL_NAME],
        ),),
        (_case(
            'contract-read-only-action', '帮我发起最近一次出差报销',
            'EMPLOYEE_READ_ONLY', 'refuse',
        ),),
        (_case(
            'contract-read-only-travel', '查询我最近一次出差记录',
            'EMPLOYEE_READ_ONLY', 'tool', [TRAVEL_RECORD_TOOL_NAME],
        ),),
        (_case(
            'contract-read-only-expense-status', '查询我的报销状态',
            'EMPLOYEE_READ_ONLY', 'tool', [EXPENSE_STATUS_TOOL_NAME],
        ),),
        (_case(
            'contract-full-leave-proposal', '请申请明天年假',
            'EMPLOYEE_FULL', 'tool', [LEAVE_PROPOSAL_TOOL_NAME],
        ),),
        (_case(
            'contract-full-expense-proposal', '帮我发起最近一次出差报销',
            'EMPLOYEE_FULL', 'tool', [EXPENSE_PROPOSAL_TOOL_NAME],
        ),),
    ],
)
def test_required_capability_contract_scenarios(case):
    report = _evaluate_contract_case(case)

    assert report['results'][0]['runs'][0]['passed'] is True


def test_unauthenticated_profile_is_not_a_formal_planner_case():
    unauthenticated_case = _case(
        'contract-unauthenticated', '查询我的年假余额',
        'UNAUTHENTICATED', 'refuse',
    )

    report = evaluate_cases(
        [unauthenticated_case],
        runs=1,
        llm_call_factory=lambda _case: pytest.fail('UNAUTHENTICATED must not call Planner'),
    )

    assert report['dataset']['planner_case_count'] == 0
    assert report['dataset']['excluded_cases'] == [{
        'id': 'contract-unauthenticated',
        'runtime_profile': 'UNAUTHENTICATED',
        'reason': '生产 Java 身份链不会进入正式 Planner',
    }]
    assert report['results'] == []


def test_routing_corpus_uses_explicit_production_identity_profiles():
    cases = load_cases()
    profiles = {case['runtime_profile'] for case in cases}

    assert 'anonymous_or_missing_identity' not in profiles
    assert {
        'UNAUTHENTICATED',
        'AUTHENTICATED_WITHOUT_EMPLOYEE_ID',
        'EMPLOYEE_READ_ONLY',
        'EMPLOYEE_FULL',
    } <= profiles
