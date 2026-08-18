"""test_agent_eval.py —— Agent Eval P0 确定性回归测试

Agent Loop 负责"做"，Agent Eval 负责判断"做得对不对"。
本测试通过注入 mock/stub Planner 响应与 Tool 结果驱动 Agent Loop，
不依赖真实 DeepSeek 网络调用，CI 中可重复、确定性执行。
"""

from app.agent_eval.cases import AGENT_EVAL_CASES, AgentEvalCase
from app.agent_eval.runner import run_agent_eval

MIN_CASE_COUNT = 15


def test_case_count_meets_p0_requirement():
    assert len(AGENT_EVAL_CASES) >= MIN_CASE_COUNT
    case_ids = [c.case_id for c in AGENT_EVAL_CASES]
    assert len(set(case_ids)) == len(case_ids), 'case_id 必须唯一'


def test_all_cases_pass_with_perfect_metrics():
    report = run_agent_eval()
    assert report['total'] == len(AGENT_EVAL_CASES)
    assert report['passed'] == report['total'], _failure_detail(report)
    metrics = report['metrics']
    assert metrics['expected_outcome_match_rate'] == 1.0
    assert metrics['tool_sequence_match_rate'] == 1.0
    assert metrics['unauthorized_tool_rate'] == 0.0
    assert metrics['budget_violation_rate'] == 0.0
    assert metrics['average_step_count'] > 0
    assert 0 <= metrics['average_tool_call_count'] <= metrics['average_step_count']


def test_case_coverage_categories():
    """回归集必须覆盖要求的全部场景类别。"""
    by_id = {c.case_id: c for c in AGENT_EVAL_CASES}
    cases = AGENT_EVAL_CASES

    assert any(c.case_id.startswith('001') for c in cases)          # 单 RAG
    assert any(c.case_id.startswith('003') for c in cases)          # 单 Eval
    assert '005-rag-then-eval' in by_id                             # RAG → Eval
    assert '006-eval-then-rag' in by_id                             # Eval → RAG
    assert '007-direct-finish' in by_id                             # finish
    assert '008-direct-refuse' in by_id                             # refuse
    assert '009-eval-denied-without-permission' in by_id            # 权限拒绝
    assert '010-safety-guard-blocks' in by_id                       # Safety Guard
    assert '011-tool-error-then-finish' in by_id                    # Tool 异常合理结束
    assert '013-repeated-call-blocked' in by_id                     # 重复调用阻止
    assert '014-step-budget-exhausted' in by_id                     # step budget
    assert '015-tool-budget-exhausted' in by_id                     # tool budget
    assert '016-leave-proposal-tool' in by_id                       # Action 经 leave_proposal_tool 走受控链路


def test_report_identifies_failing_case_with_actuals():
    """Eval 结果必须能定位失败 Case，并显示实际行为字段。"""
    bad = AgentEvalCase(
        case_id='broken-case',
        question='你好',
        expected_stop_reason='refused',
        expected_tool_sequence=(),
        max_step_count=1,
        max_tool_call_count=1,
        planner_responses=(
            '{"action":"finish","answer":"x","reason_code":"task_complete"}',
        ),
    )
    report = run_agent_eval([bad])
    result = report['results'][0]
    assert result['passed'] is False
    assert result['case_id'] == 'broken-case'
    assert result['actual_stop_reason'] == 'task_complete'
    assert result['actual_tool_sequence'] == []
    assert result['actual_step_count'] == 1
    assert result['actual_tool_call_count'] == 0
    assert any('stop_reason' in f for f in result['failures'])
    assert report['metrics']['expected_outcome_match_rate'] == 0.0


def _failure_detail(report) -> str:
    lines = ['失败 Case 明细:']
    for r in report['results']:
        if not r['passed']:
            lines.append(
                f"  {r['case_id']}: stop_reason={r['actual_stop_reason']!r} "
                f"sequence={r['actual_tool_sequence']} "
                f"step={r['actual_step_count']} tool_calls={r['actual_tool_call_count']} "
                f"route={r['actual_route']!r} failures={r['failures']}")
    return '\n'.join(lines)
