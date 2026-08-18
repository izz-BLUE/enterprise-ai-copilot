"""agent_eval_runner.py —— Agent Eval Runner

确定性回归：注入 mock/stub Planner 响应与 Tool 结果驱动 Agent Loop，
验证"Agent 是否正确完成任务、正确选择 Tool、遵守权限与预算"。

指标定义：
  expected_outcome_match_rate = 期望 stop_reason（含期望 route 时）匹配的 Case 占比
  tool_sequence_match_rate= 实际执行 Tool 序列与期望序列一致的 Case 占比
  unauthorized_tool_rate  = 未授权却实际执行了 Tool 的 Case 占比（应恒为 0）
  budget_violation_rate   = step_count 或 tool_call_count 超上限的 Case 占比
  average_step_count      = 全部 Case 的平均 Planner 决策次数
  average_tool_call_count = 全部 Case 的平均实际 Tool 执行次数

失败定位：每个 Case 输出 actual tool sequence / stop_reason /
step_count / tool_call_count 与逐项失败原因。
"""

from contextlib import ExitStack
from unittest.mock import Mock, patch

from app.agent_eval.cases import AGENT_EVAL_CASES, AgentEvalCase
from app.agents.langgraph_agent import run_langgraph_agent

_ALL_TOOL_NAMES = (
    'rag_answer_tool',
    'eval_report_tool',
    'leave_balance_tool',
    'leave_request_tool',
    'leave_proposal_tool',
)


def _executed_sequence(tool_history: list) -> list[str]:
    """实际发起执行的 Tool 序列（success/error 计数；blocked 不算执行）。"""
    return [
        entry['tool_name']
        for entry in tool_history
        if entry.get('status') in ('success', 'error')
    ]


def run_single_case(case: AgentEvalCase) -> dict:
    """运行单条 Case，返回实际状态与判定结果（异常时返回 runner 错误）。"""
    result = {
        'case_id': case.case_id,
        'passed': False,
        'actual_stop_reason': '',
        'actual_tool_sequence': [],
        'actual_step_count': 0,
        'actual_tool_call_count': 0,
        'actual_route': '',
        'failures': [],
    }

    with ExitStack() as stack:
        llm = Mock()
        if case.planner_error is not None:
            llm.side_effect = case.planner_error
        else:
            llm.side_effect = list(case.planner_responses)
        stack.enter_context(patch('app.agents.planner_node.call_llm', llm))

        # 所有 Tool 一律 Mock：注入 stub 的按 stub 行为，未注入的被调用即失败
        for name in _ALL_TOOL_NAMES:
            tool_mock = Mock()
            stub = case.tool_stubs.get(name)
            if isinstance(stub, Exception):
                tool_mock.invoke.side_effect = stub
            elif stub is not None:
                tool_mock.invoke.return_value = stub
            else:
                tool_mock.invoke.side_effect = AssertionError(
                    f'case {case.case_id} 未 stub 工具 {name}，但 Executor 发起执行')
            stack.enter_context(patch(f'app.agents.tool_executor_node.{name}', tool_mock))

        if case.safety_blocked:
            stack.enter_context(patch(
                'app.agents.langgraph_agent.check_user_query_safety',
                return_value={
                    'safe': False, 'category': 'policy_bypass',
                    'reason': 'blocked', 'message': '拒绝',
                },
            ))

        try:
            state = run_langgraph_agent(
                case.question,
                allow_eval=case.allow_eval,
                allow_business_actions=case.allow_business_actions,
                business_date=case.business_date,
                trace_id=f'eval-{case.case_id}',
                use_planner=True,
            )
        except Exception as exc:  # 回归中不应发生；发生则记为明确失败
            result['failures'].append(f'runner error: {type(exc).__name__}: {exc}')
            return result

    result['actual_stop_reason'] = state.get('stop_reason', '')
    result['actual_tool_sequence'] = _executed_sequence(state.get('tool_history', []))
    result['actual_step_count'] = state.get('step_count', 0)
    result['actual_tool_call_count'] = state.get('tool_call_count', 0)
    result['actual_route'] = state.get('route', '')

    failures = result['failures']
    if result['actual_stop_reason'] != case.expected_stop_reason:
        failures.append(
            f"stop_reason: expected {case.expected_stop_reason!r}, "
            f"actual {result['actual_stop_reason']!r}")
    if result['actual_tool_sequence'] != list(case.expected_tool_sequence):
        failures.append(
            f"tool_sequence: expected {list(case.expected_tool_sequence)}, "
            f"actual {result['actual_tool_sequence']}")
    if result['actual_step_count'] > case.max_step_count:
        failures.append(
            f"step_count: {result['actual_step_count']} 超过上限 {case.max_step_count}")
    if result['actual_tool_call_count'] > case.max_tool_call_count:
        failures.append(
            f"tool_call_count: {result['actual_tool_call_count']} 超过上限 {case.max_tool_call_count}")
    if case.expected_route is not None and result['actual_route'] != case.expected_route:
        failures.append(
            f"route: expected {case.expected_route!r}, actual {result['actual_route']!r}")
    if case.expected_planner_calls is not None:
        actual_calls = llm.call_count
        if actual_calls != case.expected_planner_calls:
            failures.append(
                f"planner_calls: expected {case.expected_planner_calls}, actual {actual_calls}")

    result['passed'] = not failures
    return result


def run_agent_eval(cases: list[AgentEvalCase] | None = None) -> dict:
    """运行全部 Eval Case，返回结果列表与六项指标。"""
    cases = AGENT_EVAL_CASES if cases is None else cases
    results = [run_single_case(c) for c in cases]
    total = max(len(results), 1)

    def rate(condition) -> float:
        return sum(1 for r in results if condition(r)) / total

    def task_ok(r: dict) -> bool:
        case = _case_by_id(cases, r['case_id'])
        if r['actual_stop_reason'] != case.expected_stop_reason:
            return False
        return case.expected_route is None or r['actual_route'] == case.expected_route

    expected_outcome_match = rate(task_ok)
    sequence_match = rate(
        lambda r: r['actual_tool_sequence'] == list(
            _case_by_id(cases, r['case_id']).expected_tool_sequence
        )
    )
    unauthorized = rate(lambda r: _is_unauthorized(r, cases))
    budget_ok = rate(lambda r: not _is_budget_violation(r, cases))
    avg_steps = sum(r['actual_step_count'] for r in results) / total
    avg_tool_calls = sum(r['actual_tool_call_count'] for r in results) / total

    return {
        'total': len(results),
        'passed': sum(1 for r in results if r['passed']),
        'results': results,
        'metrics': {
            'expected_outcome_match_rate': round(expected_outcome_match, 4),
            'tool_sequence_match_rate': round(sequence_match, 4),
            'unauthorized_tool_rate': round(unauthorized, 4),
            'budget_violation_rate': round(1 - budget_ok, 4),
            'average_step_count': round(avg_steps, 2),
            'average_tool_call_count': round(avg_tool_calls, 2),
        },
    }


def _case_by_id(cases: list[AgentEvalCase], case_id: str) -> AgentEvalCase:
    return next(c for c in cases if c.case_id == case_id)


def _is_unauthorized(result: dict, cases: list[AgentEvalCase]) -> bool:
    case = _case_by_id(cases, result['case_id'])
    return (not case.allow_eval
            and 'eval_report_tool' in result['actual_tool_sequence'])


def _is_budget_violation(result: dict, cases: list[AgentEvalCase]) -> bool:
    case = _case_by_id(cases, result['case_id'])
    return (result['actual_step_count'] > case.max_step_count
            or result['actual_tool_call_count'] > case.max_tool_call_count)
