"""Evaluate the first Planner routing decision without executing any Tool.

The evaluator deliberately stops after building the formal capability-gated
candidate set and validating one PlannerDecision.  It does not construct a
LangGraph, invoke a Workflow Guard, call a Tool, or persist state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

_AGENT_PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENT_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_PYTHON_ROOT))

# Production startup intentionally fails closed when the checkpoint DSN is
# absent.  This routing-only evaluator never opens a checkpoint, but imports
# the same Planner modules; a non-routable placeholder keeps the import
# self-contained without accepting or contacting a real database.
if not os.environ.get('LANGGRAPH_CHECKPOINT_DSN'):
    os.environ['LANGGRAPH_CHECKPOINT_DSN'] = 'postgresql://routing-eval.invalid/checkpoint'

# The path bootstrap above is needed when this file is run directly.
# ruff: noqa: E402
from pydantic import ValidationError

from app.agents.planner_node import (
    authorized_tools,
    build_planner_prompt,
    build_planner_system_prompt,
)
from app.agents.tool_catalog import TOOL_CATALOG
from app.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
)
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
    PlannerDecision,
    PlannerDecisionError,
)
from app.services.llm_service import LLMProviderError, call_llm

DATASET_PATH = Path(__file__).with_name('routing_cases.jsonl')
DEFAULT_REPORT_PATH = Path('routing_eval_report.json')
EVAL_EMPLOYEE_ID = 'D1-EVAL-EMPLOYEE'
EVAL_JAVA_URL = 'http://d1-eval-java.invalid'
EVAL_JAVA_TOKEN = 'd1-eval-internal-token'
EVAL_MCP_URL = 'http://d1-eval-mcp.invalid'

LLMCall = Callable[..., str]


RUNTIME_PROFILES: dict[str, dict[str, Any]] = {
    'EMPLOYEE_FULL': {
        'employee_id': EVAL_EMPLOYEE_ID,
        'allow_eval': False,
        'allow_business_actions': True,
        'java_base_url': EVAL_JAVA_URL,
        'java_internal_token': EVAL_JAVA_TOKEN,
        'enterprise_oa_mcp_url': EVAL_MCP_URL,
    },
    'EMPLOYEE_READ_ONLY': {
        'employee_id': EVAL_EMPLOYEE_ID,
        'allow_eval': False,
        'allow_business_actions': False,
        'java_base_url': EVAL_JAVA_URL,
        'java_internal_token': EVAL_JAVA_TOKEN,
        'enterprise_oa_mcp_url': EVAL_MCP_URL,
    },
    'ADMIN_EVAL': {
        'employee_id': EVAL_EMPLOYEE_ID,
        'allow_eval': True,
        'allow_business_actions': True,
        'java_base_url': EVAL_JAVA_URL,
        'java_internal_token': EVAL_JAVA_TOKEN,
        'enterprise_oa_mcp_url': EVAL_MCP_URL,
    },
    'AUTHENTICATED_WITHOUT_EMPLOYEE_ID': {
        'employee_id': '',
        'allow_eval': True,
        'allow_business_actions': False,
        'java_base_url': EVAL_JAVA_URL,
        'java_internal_token': EVAL_JAVA_TOKEN,
        'enterprise_oa_mcp_url': EVAL_MCP_URL,
    },
    'UNAUTHENTICATED': {
        'employee_id': '',
        'allow_eval': False,
        'allow_business_actions': False,
        'java_base_url': '',
        'java_internal_token': '',
        'enterprise_oa_mcp_url': '',
    },
}

NON_PLANNER_PROFILE_NAMES = frozenset({'UNAUTHENTICATED'})


_READ_TOOLS = frozenset({
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
})
_PROPOSAL_TOOLS = frozenset({LEAVE_PROPOSAL_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME})
_KNOWLEDGE_TOOLS = frozenset({RAG_TOOL_NAME, EVAL_TOOL_NAME})
_EXPENSE_TOOLS = frozenset({
    TRAVEL_RECORD_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
})
_LEAVE_TOOLS = frozenset({
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
})

FAILURE_TYPES = (
    'KNOWLEDGE_TO_LIVE_READ',
    'LIVE_READ_TO_RAG',
    'READ_TO_PROPOSAL',
    'PROPOSAL_TO_READ',
    'WRONG_DOMAIN',
    'PREMATURE_FINISH',
    'UNEXPECTED_REFUSE',
    'UNAUTHORIZED_SELECTION',
    'INVALID_SCHEMA',
    'UNKNOWN_TOOL',
    'OTHER',
)


def load_cases(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate the JSONL corpus."""
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'第 {line_number} 行不是合法 JSON') from exc
        if not isinstance(case, dict):
            raise ValueError(f'第 {line_number} 行必须是 JSON object')
        required = {'id', 'category', 'question', 'runtime_profile', 'expected'}
        missing = required - set(case)
        if missing:
            raise ValueError(f'第 {line_number} 行缺少字段: {sorted(missing)}')
        case_id = case['id']
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
            raise ValueError(f'第 {line_number} 行 id 无效或重复: {case_id!r}')
        if case['runtime_profile'] not in RUNTIME_PROFILES:
            raise ValueError(f'第 {line_number} 行 runtime_profile 无效: {case["runtime_profile"]!r}')
        expected = case['expected']
        if (
            not isinstance(expected, dict)
            or expected.get('action') not in {'tool', 'finish', 'refuse'}
            or not isinstance(expected.get('tool_names'), list)
        ):
            raise ValueError(f'第 {line_number} 行 expected contract 无效')
        if expected['action'] != 'tool' and expected['tool_names']:
            raise ValueError(f'第 {line_number} 行非 tool expected 不得声明 tool_names')
        if any(name not in TOOL_CATALOG.tool_names for name in expected['tool_names']):
            raise ValueError(f'第 {line_number} 行 expected 包含未注册 Tool')
        seen_ids.add(case_id)
        cases.append(case)
    return cases


def select_cases(
    cases: list[dict[str, Any]],
    *,
    limit: int | None = None,
    categories: list[str] | None = None,
    case_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Apply CLI-only corpus filters without changing scoring semantics."""
    selected = cases
    if categories:
        category_set = set(categories)
        selected = [case for case in selected if case['category'] in category_set]
    if case_ids:
        case_id_set = set(case_ids)
        known_ids = {case['id'] for case in cases}
        unknown_ids = case_id_set - known_ids
        if unknown_ids:
            raise ValueError(f'不存在的 case id: {sorted(unknown_ids)}')
        selected = [case for case in selected if case['id'] in case_id_set]
    if limit is not None:
        if limit < 1:
            raise ValueError('limit 必须大于等于 1')
        selected = selected[:limit]
    if not selected:
        raise ValueError('过滤条件未选中任何 case')
    return selected


def authorized_tools_for_case(case: dict[str, Any]) -> list[str]:
    """Compute the candidate set from the formal Capability Gate."""
    if case['runtime_profile'] in NON_PLANNER_PROFILE_NAMES:
        return []
    profile = RUNTIME_PROFILES[case['runtime_profile']]
    return authorized_tools(**profile)


def build_routing_prompts(case: dict[str, Any]) -> tuple[list[str], str, str]:
    """Build the exact first-turn candidate set and Planner prompts."""
    if case['runtime_profile'] in NON_PLANNER_PROFILE_NAMES:
        raise ValueError(
            f"runtime profile {case['runtime_profile']} 不进入正式 Planner routing eval"
        )
    tools = authorized_tools_for_case(case)
    system_prompt = build_planner_system_prompt(tools)
    user_prompt = build_planner_prompt(
        case['question'],
        tools,
        [],
        '',
        1,
        None,
        [],
        None,
        None,
    )
    return tools, system_prompt, user_prompt


def _stub_arguments(tool_name: str, question: str) -> dict[str, Any]:
    if tool_name == RAG_TOOL_NAME:
        return {'question': question}
    if tool_name == EVAL_TOOL_NAME:
        return {'report_type': 'all'}
    if tool_name == LEAVE_REQUEST_TOOL_NAME:
        return {'limit': 10}
    if tool_name == INVOICE_VERIFY_TOOL_NAME:
        return {'invoice_id': 'INV-EVAL-001'}
    if tool_name == EXPENSE_STATUS_TOOL_NAME:
        return {}
    return {}


def _stub_payload(case: dict[str, Any]) -> dict[str, Any]:
    expected = case['expected']
    action = expected['action']
    if action == 'tool':
        tool_name = expected['tool_names'][0]
        return {
            'action': 'tool',
            'tool_name': tool_name,
            'arguments': _stub_arguments(tool_name, case['question']),
            'reason_code': TOOL_CATALOG.prompt_spec(tool_name).reason_code,
            'expense_reason': None,
        }
    if action == 'finish':
        return {
            'action': 'finish',
            'answer': '评估用完成响应。',
            'reason_code': 'task_complete',
            'expense_reason': None,
        }
    return {
        'action': 'refuse',
        'answer': '评估用拒绝响应。',
        'reason_code': 'not_allowed',
        'expense_reason': None,
    }


def stub_llm_for_case(case: dict[str, Any]) -> LLMCall:
    """Return a deterministic, schema-valid fake LLM for unit infrastructure."""
    payload = json.dumps(_stub_payload(case), ensure_ascii=False)

    def _call(_system_prompt: str, _user_prompt: str, **_options: Any) -> str:
        return payload

    return _call


def _partial_payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _parse_decision(raw: str) -> dict[str, Any]:
    """Parse only the formal Planner schema; return a redacted observation."""
    payload: object = None
    try:
        payload = json.loads(raw)
        decision = PlannerDecision.model_validate(payload)
        decision.validate_decision()
    except json.JSONDecodeError:
        return {
            'schema_valid': False,
            'action': None,
            'tool_name': None,
            'failure_type': 'INVALID_SCHEMA',
        }
    except (ValidationError, PlannerDecisionError):
        partial = _partial_payload(payload)
        action = partial.get('action')
        tool_name = partial.get('tool_name')
        failure_type = (
            'UNKNOWN_TOOL'
            if action == 'tool'
            and isinstance(tool_name, str)
            and tool_name not in TOOL_CATALOG.tool_names
            else 'INVALID_SCHEMA'
        )
        return {
            'schema_valid': False,
            'action': action if action in {'tool', 'finish', 'refuse'} else None,
            'tool_name': tool_name if isinstance(tool_name, str) else None,
            'failure_type': failure_type,
        }
    return {
        'schema_valid': True,
        'action': decision.action,
        'tool_name': decision.tool_name,
        'failure_type': None,
    }


def _tool_kind(tool_name: str | None) -> str | None:
    if tool_name in _KNOWLEDGE_TOOLS:
        return 'knowledge'
    if tool_name in _READ_TOOLS:
        return 'read'
    if tool_name in _PROPOSAL_TOOLS:
        return 'proposal'
    return None


def _expected_tool_kind(expected_tools: Iterable[str]) -> str | None:
    kinds = {_tool_kind(name) for name in expected_tools}
    return next(iter(kinds)) if len(kinds) == 1 else None


def classify_failure(
    case: dict[str, Any],
    observation: dict[str, Any],
    authorized: list[str],
) -> str | None:
    """Map a failed first decision to a stable routing failure taxonomy."""
    action = observation.get('action')
    tool_name = observation.get('tool_name')
    if observation.get('failure_type') == 'UNKNOWN_TOOL':
        return 'UNKNOWN_TOOL'
    if action == 'tool' and tool_name not in authorized:
        return 'UNAUTHORIZED_SELECTION'
    if observation.get('failure_type') == 'INVALID_SCHEMA':
        return 'INVALID_SCHEMA'

    expected = case['expected']
    expected_action = expected['action']
    expected_tools = expected['tool_names']
    if expected_action != 'tool':
        return 'UNEXPECTED_REFUSE' if action == 'refuse' else 'PREMATURE_FINISH'
    if action == 'finish':
        return 'PREMATURE_FINISH'
    if action == 'refuse':
        return 'UNEXPECTED_REFUSE'

    expected_kind = _expected_tool_kind(expected_tools)
    actual_kind = _tool_kind(tool_name)
    if expected_kind == 'knowledge' and actual_kind == 'read':
        return 'KNOWLEDGE_TO_LIVE_READ'
    if expected_kind == 'read' and actual_kind == 'knowledge':
        return 'LIVE_READ_TO_RAG'
    if expected_kind == 'read' and actual_kind == 'proposal':
        return 'READ_TO_PROPOSAL'
    if expected_kind == 'proposal' and actual_kind in {'knowledge', 'read'}:
        return 'PROPOSAL_TO_READ'
    expected_domain = 'leave' if set(expected_tools) <= _LEAVE_TOOLS else 'expense'
    actual_domain = 'leave' if tool_name in _LEAVE_TOOLS else 'expense' if tool_name in _EXPENSE_TOOLS else None
    if actual_domain is not None and actual_domain != expected_domain:
        return 'WRONG_DOMAIN'
    return 'OTHER'


def score_decision(
    case: dict[str, Any],
    observation: dict[str, Any],
    authorized: list[str],
) -> dict[str, Any]:
    expected = case['expected']
    action = observation.get('action')
    tool_name = observation.get('tool_name')
    schema_valid = bool(observation.get('schema_valid'))
    unauthorized = action == 'tool' and tool_name not in authorized
    if expected['action'] == 'tool':
        semantic_match = action == 'tool' and tool_name in expected['tool_names']
    else:
        semantic_match = action == expected['action'] and tool_name is None
    passed = schema_valid and semantic_match and not unauthorized
    return {
        **observation,
        'authorized': tool_name in authorized if action == 'tool' else True,
        'unauthorized_selection': unauthorized,
        'semantic_match': semantic_match,
        'passed': passed,
        'failure_type': None if passed else classify_failure(case, observation, authorized),
    }


def _dataset_metadata(
    cases: list[dict[str, Any]],
    planner_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    excluded = [
        {
            'id': case['id'],
            'runtime_profile': case['runtime_profile'],
            'reason': '生产 Java 身份链不会进入正式 Planner',
        }
        for case in cases
        if case not in planner_cases
    ]
    return {
        'path': str(DATASET_PATH.name),
        'case_count': len(cases),
        'planner_case_count': len(planner_cases),
        'excluded_cases': excluded,
        'category_counts': dict(sorted(Counter(case['category'] for case in cases).items())),
    }


def _metric(correct: int, total: int) -> dict[str, Any]:
    return {
        'correct': correct,
        'total': total,
        'rate': round(correct / total, 6) if total else None,
    }


def _run_rows(results: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (case_result, run_result)
        for case_result in results
        for run_result in case_result['runs']
    ]


def _build_metrics(results: list[dict[str, Any]], runs_per_case: int) -> dict[str, Any]:
    rows = _run_rows(results)

    def selected(predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        return [run for case, run in rows if predicate(case) and run['passed']]

    def total_for(predicate: Callable[[dict[str, Any]], bool]) -> int:
        return sum(1 for case, _run in rows if predicate(case))

    def metric_for(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        return _metric(len(selected(predicate)), total_for(predicate))

    def tool_cases(case: dict[str, Any]) -> bool:
        return case['expected']['action'] == 'tool'

    def knowledge_cases(case: dict[str, Any]) -> bool:
        return case['category'] in {'knowledge_rag', 'expense_knowledge'}

    def live_read_cases(case: dict[str, Any]) -> bool:
        return case['category'] in {'leave_live_read', 'expense_live_read'}

    def read_proposal_cases(case: dict[str, Any]) -> bool:
        return case['category'] in {
            'leave_live_read', 'expense_live_read', 'leave_proposal', 'expense_proposal',
        }

    def leave_cases(case: dict[str, Any]) -> bool:
        return case['category'].startswith('leave_')

    def expense_cases(case: dict[str, Any]) -> bool:
        return case['category'].startswith('expense_')

    def cross_domain_cases(case: dict[str, Any]) -> bool:
        return case['category'] == 'cross_domain'

    def permission_cases(case: dict[str, Any]) -> bool:
        return case['category'] == 'permission_boundary'

    def unsupported_cases(case: dict[str, Any]) -> bool:
        return case['category'] == 'negative_unsupported'

    pass_count_by_case = {
        case_result['id']: sum(1 for run in case_result['runs'] if run['passed'])
        for case_result in results
    }
    buckets = Counter(f'{passed}/{runs_per_case}' for passed in pass_count_by_case.values())
    stability_buckets = {
        f'{passed}/{runs_per_case}': buckets.get(f'{passed}/{runs_per_case}', 0)
        for passed in range(runs_per_case, -1, -1)
    }
    unauthorized_count = sum(1 for _case, run in rows if run['unauthorized_selection'])
    unavailable_capability_fallback_count = sum(
        1
        for case, run in rows
        if case['category'] == 'permission_boundary'
        and case['expected']['action'] == 'refuse'
        and run['action'] == 'tool'
        and run['authorized']
    )
    rag_substitution_count = sum(
        1
        for case, run in rows
        if case['runtime_profile'] == 'AUTHENTICATED_WITHOUT_EMPLOYEE_ID'
        and case['expected']['action'] == 'refuse'
        and run['action'] == 'tool'
        and run['tool_name'] == RAG_TOOL_NAME
    )
    readonly_prerequisite_substitution_count = sum(
        1
        for case, run in rows
        if case['runtime_profile'] == 'EMPLOYEE_READ_ONLY'
        and case['expected']['action'] == 'refuse'
        and run['action'] == 'tool'
        and run['tool_name'] in _READ_TOOLS
    )
    failure_counts = Counter(
        run['failure_type']
        for _case, run in rows
        if run['failure_type'] is not None
    )
    return {
        'overall_tool_selection_accuracy': metric_for(tool_cases),
        'action_accuracy': _metric(sum(1 for _case, run in rows if run['semantic_match']), len(rows)),
        'knowledge_rag_accuracy': metric_for(knowledge_cases),
        'live_read_accuracy': metric_for(live_read_cases),
        'read_vs_proposal_accuracy': metric_for(read_proposal_cases),
        'leave_accuracy': metric_for(leave_cases),
        'expense_accuracy': metric_for(expense_cases),
        'cross_domain_first_step_accuracy': metric_for(cross_domain_cases),
        'permission_cases_accuracy': metric_for(permission_cases),
        'unsupported_accuracy': metric_for(unsupported_cases),
        'schema_valid_rate': _metric(
            sum(1 for _case, run in rows if run['schema_valid']), len(rows)
        ),
        'stability_rate': _metric(
            sum(1 for passed in pass_count_by_case.values() if passed == runs_per_case),
            len(pass_count_by_case),
        ),
        'perfect_stability': {
            'buckets': stability_buckets,
            'case_count': len(pass_count_by_case),
            'runs_per_case': runs_per_case,
        },
        'unauthorized_selection_rate': _metric(unauthorized_count, len(rows)),
        'unavailable_capability_fallback_count': unavailable_capability_fallback_count,
        'rag_substitution_count': rag_substitution_count,
        'readonly_prerequisite_substitution_count': readonly_prerequisite_substitution_count,
        'failure_type_counts': {
            failure_type: failure_counts.get(failure_type, 0)
            for failure_type in FAILURE_TYPES
        },
    }


def _recommendation(metrics: dict[str, Any], live_completed: bool) -> str:
    if not live_completed:
        return 'EVAL_NOT_RUN'
    required = (
        ('overall_tool_selection_accuracy', 0.95),
        ('knowledge_rag_accuracy', 0.95),
        ('live_read_accuracy', 0.95),
        ('read_vs_proposal_accuracy', 0.98),
        ('schema_valid_rate', 0.99),
    )
    if any(
        metrics[name]['rate'] is None or metrics[name]['rate'] < threshold
        for name, threshold in required
    ) or metrics['unauthorized_selection_rate']['correct'] != 0 or any(
        metrics[name] != 0
        for name in (
            'unavailable_capability_fallback_count',
            'rag_substitution_count',
            'readonly_prerequisite_substitution_count',
        )
    ):
        return 'NEEDS_ROUTING_TUNING'
    return 'PASS'


def evaluate_cases(
    cases: list[dict[str, Any]],
    *,
    runs: int = 1,
    live: bool = False,
    llm_call_factory: Callable[[dict[str, Any]], LLMCall] | None = None,
) -> dict[str, Any]:
    """Run one or more first-decision evaluations per corpus case."""
    if runs < 1:
        raise ValueError('runs 必须大于等于 1')
    if llm_call_factory is None:
        llm_call_factory = (lambda _case: call_llm) if live else stub_llm_for_case

    planner_cases = [
        case for case in cases
        if case['runtime_profile'] not in NON_PLANNER_PROFILE_NAMES
    ]
    results: list[dict[str, Any]] = []
    live_error: str | None = None
    for case in planner_cases:
        authorized, system_prompt, user_prompt = build_routing_prompts(case)
        run_results: list[dict[str, Any]] = []
        llm_call = llm_call_factory(case)
        for run_index in range(1, runs + 1):
            try:
                raw = llm_call(
                    system_prompt,
                    user_prompt,
                    response_format={'type': 'json_object'},
                    thinking=False,
                )
                observation = _parse_decision(raw)
            except LLMProviderError as exc:
                # Only retain the provider's stable error code; never report
                # endpoint, request body, token, or provider response text.
                live_error = f'provider_error:{exc.code}'
                observation = {
                    'schema_valid': False,
                    'action': None,
                    'tool_name': None,
                    'failure_type': 'OTHER',
                }
            except Exception:
                live_error = 'llm_call_error'
                observation = {
                    'schema_valid': False,
                    'action': None,
                    'tool_name': None,
                    'failure_type': 'OTHER',
                }
            scored = score_decision(case, observation, authorized)
            run_results.append({
                'run': run_index,
                'action': scored['action'],
                'tool_name': scored['tool_name'],
                'schema_valid': scored['schema_valid'],
                'authorized': scored['authorized'],
                'unauthorized_selection': scored['unauthorized_selection'],
                'semantic_match': scored['semantic_match'],
                'passed': scored['passed'],
                'failure_type': scored['failure_type'],
            })
        results.append({
            'id': case['id'],
            'category': case['category'],
            'question': case['question'],
            'runtime_profile': case['runtime_profile'],
            'authorized_tools': authorized,
            'capability_status': TOOL_CATALOG.capability_status(authorized),
            'expected': case['expected'],
            'runs': run_results,
        })

    live_completed = live and live_error is None
    report = {
        'report_version': 'phase-d1-routing-eval-v1',
        'dataset': _dataset_metadata(cases, planner_cases),
        'run': {
            'mode': 'live' if live else 'stub',
            'runs_per_case': runs,
            'total_runs': len(planner_cases) * runs,
            'status': 'COMPLETED' if live_completed or not live else 'FAILED',
        },
        'live_eval': {
            'status': 'COMPLETED' if live_completed else 'NOT_RUN',
            'reason': None if live_completed else (
                live_error or '未启用 --live；stub 结果仅用于评估器单元/结构验证'
            ),
        },
        'safety': {
            'raw_prompts_saved': False,
            'raw_llm_responses_saved': False,
            'real_identity_or_token_used': False,
            'tools_executed': False,
            'state_persisted': False,
        },
        'metrics': _build_metrics(results, runs),
        'results': results,
    }
    report['recommendation'] = _recommendation(report['metrics'], live_completed)
    report['final_verdict'] = 'READY' if report['recommendation'] == 'PASS' else 'NOT READY'
    return report


def _missing_live_config() -> list[str]:
    missing: list[str] = []
    if not DEEPSEEK_API_KEY:
        missing.append('DEEPSEEK_API_KEY')
    if not DEEPSEEK_BASE_URL:
        missing.append('DEEPSEEK_BASE_URL')
    if not DEEPSEEK_MODEL:
        missing.append('DEEPSEEK_MODEL')
    return missing


def _not_run_report(cases: list[dict[str, Any]], reason: str, runs: int) -> dict[str, Any]:
    planner_cases = [
        case for case in cases
        if case['runtime_profile'] not in NON_PLANNER_PROFILE_NAMES
    ]
    return {
        'report_version': 'phase-d1-routing-eval-v1',
        'dataset': _dataset_metadata(cases, planner_cases),
        'run': {'mode': 'live', 'runs_per_case': runs, 'total_runs': 0, 'status': 'NOT_RUN'},
        'live_eval': {'status': 'NOT_RUN', 'reason': reason},
        'safety': {
            'raw_prompts_saved': False,
            'raw_llm_responses_saved': False,
            'real_identity_or_token_used': False,
            'tools_executed': False,
            'state_persisted': False,
        },
        'metrics': None,
        'results': [],
        'recommendation': 'EVAL_NOT_RUN',
        'final_verdict': 'NOT READY',
    }


def _print_summary(report: dict[str, Any]) -> None:
    print('Phase D1 Semantic Routing Eval')
    print(
        f"cases={report['dataset']['case_count']} mode={report['run']['mode']} "
        f"planner_cases={report['dataset']['planner_case_count']} "
        f"runs_per_case={report['run']['runs_per_case']}"
    )
    live_eval = report['live_eval']
    if live_eval['status'] != 'COMPLETED':
        print(f"LIVE EVAL NOT RUN: {live_eval['reason']}")
    metrics = report['metrics']
    if metrics is not None:
        for name in (
            'overall_tool_selection_accuracy',
            'action_accuracy',
            'knowledge_rag_accuracy',
            'live_read_accuracy',
            'read_vs_proposal_accuracy',
            'schema_valid_rate',
            'stability_rate',
            'unauthorized_selection_rate',
        ):
            value = metrics[name]
            rate = 'n/a' if value['rate'] is None else f"{value['rate']:.2%}"
            print(f'{name}={rate} ({value["correct"]}/{value["total"]})')
        for name in (
            'unavailable_capability_fallback_count',
            'rag_substitution_count',
            'readonly_prerequisite_substitution_count',
        ):
            print(f'{name}={metrics[name]}')
    print(f"Recommendation={report['recommendation']} Final Verdict={report['final_verdict']}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='调用真实 Planner LLM；默认使用 deterministic stub')
    parser.add_argument('--runs', type=int, default=1, help='每条 case 的运行次数，手工 live 建议为 3')
    parser.add_argument('--dataset', type=Path, default=DATASET_PATH)
    parser.add_argument('--output', type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument('--limit', type=int, help='只运行前 N 条已过滤 case')
    parser.add_argument('--category', action='append', dest='categories', help='按 category 过滤，可重复指定')
    parser.add_argument('--case-id', action='append', dest='case_ids', help='按 case id 过滤，可重复指定')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.runs < 1:
        raise SystemExit('--runs 必须大于等于 1')
    cases = select_cases(
        load_cases(args.dataset),
        limit=args.limit,
        categories=args.categories,
        case_ids=args.case_ids,
    )
    if args.live:
        missing = _missing_live_config()
        if missing:
            report = _not_run_report(
                cases,
                f'缺少真实 LLM 配置: {", ".join(missing)}；未创建 API key、未调用模型',
                args.runs,
            )
        else:
            report = evaluate_cases(cases, runs=args.runs, live=True)
    else:
        report = evaluate_cases(cases, runs=args.runs, live=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    _print_summary(report)
    print(f'Report={args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
