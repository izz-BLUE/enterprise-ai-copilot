"""Real Eval Runner

执行流程：
  run_real_eval(cases, runs_per_case)
    → 对每个 Case 构造全新 Stub + wrap call_llm（不改模型行为）
    → 多次 invoke run_langgraph_agent
    → 每条 Run 记录 stop_reason / 序列 / 预算 / 延迟 / 失败原因
    → 聚合 12+ 项 P0 指标
    → 写 JSON 报告到 data/eval/reports/agent_real_eval_<ts>.json

设计要点：
- call_llm 通过 wrapper 包裹：捕获每次 Planner 原始 JSON 输出，但依然
  调用真实 call_llm，不替换模型行为。仅当脚本手工显式开启真实评估时
  才允许 wrapper 触发；CI 测试不会走到 wrapper 实际内部（CI 路径
  见 tests/test_agent_real_eval.py 中的 stub LLM fixture）
- 不强行 Patch 业务 Action / Safety / Refuse 任何节点
- 不进 Phoenix 网络测试路径（脚本本身不强制开启 tracing）
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator
from unittest.mock import patch

from app.agent_real_eval.cases import (
    REAL_AGENT_EVAL_CASES,
    REAL_AGENT_EVAL_SUITE_VERSION,
    RealAgentEvalCase,
    rag_topics_for_question,
)
from app.agent_real_eval.tool_stubs import make_stub
from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import MAX_PLANNER_STEPS
from app.agents.tool_executor_node import MAX_TOOL_CALLS
from app.schemas.planner_schema import EVAL_TOOL_NAME, RAG_TOOL_NAME

# Eval report_type → topic id (固定表)
_EVAL_TOPIC_BY_REPORT_TYPE = {
    'retrieval': 'retrieval',
    'generation': 'generation',
    'all': 'all',
}

# P0 评估指标中"完成态之后的冗余调用"相关：本模块的判定函数使用
FINISH_WHEN_COMPLETE_TOLERANCE = 0  # 容忍 0 个多余 Tool 调用尝试


# ── 数据结构 ────────────────────────────────────────────────

@dataclass
class RealEvalRunResult:
    """单次 Run（一个 Case × run_index）的完整明细。"""

    case_id: str
    category: str
    run_index: int
    trace_id: str
    stop_reason: str
    route: str
    executed_tool_sequence: list[str] = field(default_factory=list)
    tool_history: list[dict] = field(default_factory=list)
    step_count: int = 0
    tool_call_count: int = 0
    answer: str = ''
    answer_nonempty: bool = False
    latency_ms: int = 0
    failure_reasons: list[str] = field(default_factory=list)
    passed: bool = False
    # 规划器侧暴露：每次 Planner 输出的原始 JSON（不依赖 case 注入）
    planner_raw_outputs: list[str] = field(default_factory=list)
    # 是否在本次 Run 中出现 unauthorized 尝试或执行
    unauthorized_attempt: bool = False
    unauthorized_execution: bool = False
    finish_when_complete: bool = True  # True=未发生完成态冗余
    # 错误分类聚合（用于失败分析）
    failure_categories: list[str] = field(default_factory=list)
    # 是否 Pre-Planner 终止（Router/Safety 已在 Planner 之前拦截）
    pre_planner_terminal: bool = False
    # Run 级 sequence 命中 declared accepted_tool_sequences
    sequence_match: bool = False
    # Run 级：声明 required_call_specs 是否全部覆盖
    required_task_coverage: bool = True


@dataclass
class RealEvalCaseReport:
    """单 Case 聚合：包含 runs_per_case 条 RunResult 与稳定性判定。"""

    case_id: str
    category: str
    question: str
    runs: list[RealEvalRunResult] = field(default_factory=list)
    passed: bool = False
    stable: bool = False
    pass_count: int = 0
    required_tools_satisfied: bool = False
    # 任一 Run 是否命中 declared accepted_tool_sequences（Case 级聚合，与 run 级分离）
    sequence_matched: bool = False
    # 是否所有 Run 走完全相同的 Tool 轨迹（仅 tool_name 序列；连续重复算一次）
    trajectory_consistent: bool = True
    failure_reasons_aggregated: list[str] = field(default_factory=list)


@dataclass
class RealEvalSuiteReport:
    """整个 Real Eval 任务的报告。"""

    suite_version: str
    timestamp: str
    git_commit: str
    model: str
    temperature: float | None
    runs_per_case: int
    max_planner_steps: int
    max_tool_calls: int
    real_tools: bool
    phoenix_tracing: bool
    cases: list[RealEvalCaseReport] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    # 全局失败原因聚合
    failure_reasons_aggregated: dict[str, int] = field(default_factory=dict)
    failed_case_ids: list[str] = field(default_factory=list)
    unstable_case_ids: list[str] = field(default_factory=list)


# ── run_pass 判定 ────────────────────────────────────────────

# 用于分类 failure_reasons 的稳定错误码
_FAIL_REASON_BUCKET = {
    'stop_reason_mismatch': 'stop_reason_mismatch',
    'required_tool_missing': 'required_tool_missing',
    'forbidden_tool_executed': 'forbidden_tool_executed',
    'sequence_mismatch': 'sequence_mismatch',
    'invalid_decision': 'invalid_decision',
    'budget_violation': 'budget_violation',
    'redundant_tool_attempt': 'redundant_tool_attempt',
    'answer_empty_when_required': 'answer_empty_when_required',
}


def _executed_sequence(tool_history: list[dict]) -> list[str]:
    """真正发起执行的 Tool 序列（success / error 计数；blocked 不算执行）。"""
    return [
        entry.get('tool_name')
        for entry in tool_history
        if entry.get('status') in ('success', 'error')
    ]


def _matched_sequence(
    executed: list[str],
    accepted: tuple[tuple[str, ...] | tuple[str, ...], ...],
) -> bool:
    """匹配接受序列：精确顺序时直接等；元组集合视为顺序无关（任意排列）。

    accepted 的每个元素要么是字符串元组（视为精确序列），要么是单字符串
    元组 / 单字符串——按精确匹配判断。
    accepted 为空表示"Case 未声明序列约束"，视为永真。
    """
    if not accepted:
        return True
    for entry in accepted:
        if isinstance(entry, tuple):
            target = list(entry)
        else:  # 字符串
            target = [entry]
        if executed == target:
            return True
    return False


def _entry_topics(entry: dict) -> list[str]:
    """把一条 Tool 执行条目路由到 topic 列表（覆盖语义：一次可覆盖多个 topic）。

    rag_answer_tool：基于 arguments.question 命中 KB key 列表，可多个；
    eval_report_tool：基于 arguments.report_type 映射，单个 topic；
    推不到返回 []。
    """
    tool = entry.get('tool_name')
    args = entry.get('arguments') or {}
    if tool == RAG_TOOL_NAME:
        question = args.get('question') or args.get('original_question') or ''
        return rag_topics_for_question(question)
    if tool == EVAL_TOOL_NAME:
        report_type = args.get('report_type', '')
        # report_type=all 的实际 Observation 同时包含 retrieval + generation
        # 两个 section；因此一次 all 查询同时覆盖三个 topic（含 all 自身）。
        if report_type == 'all':
            return ['retrieval', 'generation', 'all']
        topic = _EVAL_TOPIC_BY_REPORT_TYPE.get(report_type, report_type)
        return [topic] if topic else []
    return []


# 兼容旧名：取首个 topic（用于历史 hit-path）
def _entry_topic(entry: dict) -> str | None:
    """单 topic 检索：取 _entry_topics 首个；多 topic 覆盖场景请直接用 _entry_topics。"""
    topics = _entry_topics(entry)
    return topics[0] if topics else None


def _completed_required_tools(
    required_tools: tuple[str, ...],
    executed: list[str],
    tool_history: list[dict],
) -> bool:
    """required_tools 粗粒度判断：每个 Tool 至少一次 success。"""
    if not required_tools:
        return True
    success_tools = {
        entry.get('tool_name')
        for entry in tool_history
        if entry.get('status') == 'success'
    }
    for tool in required_tools:
        if tool not in success_tools:
            return False
    return True


def _covered_specs(
    specs: tuple[tuple[str, str], ...],
    tool_history: list[dict],
) -> set[tuple[str, str]]:
    """Coverage 语义：累计所有 success Tool entry 覆盖到的 (tool_name, topic)。

    一次 RAG 调用命中"年假和报销"会同时覆盖 {annual_leave, expense} 两个 topic，
    因此一次 entry 可满足多个 spec。

    返回已覆盖的 spec 集合；调用方对照 specs 求差集即可计算 missing_specs。
    """
    covered: set[tuple[str, str]] = set()
    for entry in tool_history:
        if entry.get('status') != 'success':
            continue
        tool_name = entry.get('tool_name')
        if not tool_name:
            continue
        for topic in _entry_topics(entry):
            covered.add((tool_name, topic))
    return covered


def _completed_required_specs(
    specs: tuple[tuple[str, str], ...],
    tool_history: list[dict],
) -> bool:
    """required_call_specs coverage 判定：必须每个 (tool, topic) 都被成功覆盖。

    支持 R10 的两种合法路径：
      A. rag(annual_leave) + rag(expense)
      B. rag(annual_leave + expense)（一次 RAG 同时覆盖两个 topic）
    都视为满足 (annual_leave) + (expense) 的双 spec。
    """
    if not specs:
        return True
    return all(spec in _covered_specs(specs, tool_history) for spec in specs)


def _last_required_specs_completion_index(
    specs: tuple[tuple[str, str], ...],
    tool_history: list[dict],
) -> int | None:
    """Coverage 版：返回 specs 全部被覆盖时，最后一次"补全某 spec"的 entry 索引。

    一次 entry 命中多 topic 时，会同时标记多个 spec 的完成位置为该 idx。
    """
    if not specs:
        return None
    spec_to_idx: dict[tuple[str, str], int] = {}
    for idx, entry in enumerate(tool_history):
        if entry.get('status') != 'success':
            continue
        tool_name = entry.get('tool_name')
        if not tool_name:
            continue
        for topic in _entry_topics(entry):
            key = (tool_name, topic)
            if key in specs and key not in spec_to_idx:
                spec_to_idx[key] = idx
    if len(spec_to_idx) != len(specs):
        return None
    return max(spec_to_idx.values())


def _check_finish_when_complete(case: RealAgentEvalCase, history: list[dict]) -> bool:
    """True 表示未发生"完成态后的冗余 Tool attempt"。

    完成态定义优先用 required_call_specs（细粒度，支持多 topic）；
    若未声明则降级到 required_tools 粗粒度。
    后续若 Planner 再提出 RAG/Eval Tool attempt（success/error/blocked
    都算 attempt），则视为冗余。
    """
    if case.required_call_specs:
        last_idx = _last_required_specs_completion_index(
            case.required_call_specs, history
        )
        if last_idx is None:
            return True  # 必要子任务都还没完成 → 算不上完成态
    elif case.required_tools:
        # 粗粒度：用最早一个 required 的 first_success_index ... 不，更准确
        # 是用每个 required 的 first_success 的最大值
        last_idx = -1
        any_success = False
        for tool in case.required_tools:
            for idx, entry in enumerate(history):
                if (
                    entry.get('tool_name') == tool
                    and entry.get('status') == 'success'
                ):
                    last_idx = max(last_idx, idx)
                    any_success = True
                    break
        if not any_success:
            return True
    else:
        return True

    later_entries = history[last_idx + 1:]
    redundant_calls = [
        entry for entry in later_entries
        if entry.get('tool_name') in (RAG_TOOL_NAME, EVAL_TOOL_NAME)
    ]
    return len(redundant_calls) <= FINISH_WHEN_COMPLETE_TOLERANCE


def _detect_unauthorized(
    case: RealAgentEvalCase,
    executed: list[str],
    raw_outputs: list[str],
) -> tuple[bool, bool]:
    """返回 (attempt, execution)：

    - attempt: Planner 是否曾提出调用 EVAL_TOOL 的决策（无论权限）
    - execution: 该 Tool 是否最终被 Executor 真正发起执行

    attempt 通过解析 Planner raw JSON 检查；execution 通过 executed 序列
    检查。
    """
    attempt = False
    for raw in raw_outputs:
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get('action') == 'tool'
            and parsed.get('tool_name') == EVAL_TOOL_NAME
        ):
            attempt = True
            break

    executed_eval = EVAL_TOOL_NAME in executed
    return attempt, executed_eval


def _evaluate_run(
    case: RealAgentEvalCase,
    state: dict,
    raw_outputs: list[str],
    latency_ms: int,
) -> RealEvalRunResult:
    """对一次 run 输出做 Run Pass / 指标判定。"""
    tool_history = state.get('tool_history', []) or []
    executed = _executed_sequence(tool_history)
    answer = state.get('answer', '') or ''
    stop_reason = state.get('stop_reason', '') or ''
    step_count = state.get('step_count', 0) or 0
    tool_call_count = state.get('tool_call_count', 0) or 0
    route = state.get('route', '') or ''

    failure_reasons: list[str] = []
    failure_categories: list[str] = []

    pre_planner_terminal = (
        step_count == 0
        and not tool_history
        and route == 'refuse'
        and not raw_outputs
    )

    if case.planner_invoked and pre_planner_terminal:
        failure_reasons.append(
            'planner_not_invoked: Case 要求 Planner 参与，但 trace 显示 Planner 未运行'
        )
        failure_categories.append('planner_not_invoked')

    # 分支输出变量默认值：pre_planner_blocked 路径不会经过 else 分支赋值，
    # 先初始化避免依赖三元表达式惰性求值（行为与之前完全一致）
    _attempt_unauth_global = False
    _exec_unauth_global = False
    _finish_when_complete_global = True

    if case.pre_planner_blocked:
        if not pre_planner_terminal:
            failure_reasons.append(
                'pre_planner_expected: Case 期望 Pre-Planner 终止，但实际走 Planner 路径'
            )
            failure_categories.append('pre_planner_expected')
        else:
            if not answer.strip():
                failure_reasons.append('answer_empty_when_required')
                failure_categories.append(_FAIL_REASON_BUCKET['answer_empty_when_required'])
            forbidden_violations = [t for t in case.forbidden_tools if t in executed]
            if forbidden_violations:
                failure_reasons.append(
                    f'forbidden_tool_executed: {forbidden_violations}'
                )
                failure_categories.append(_FAIL_REASON_BUCKET['forbidden_tool_executed'])
            _, exec_unauth = _detect_unauthorized(case, executed, raw_outputs)
            if exec_unauth and not case.allow_eval:
                failure_reasons.append(
                    f'unauthorized_tool_execution: {EVAL_TOOL_NAME} 被实际执行'
                )
                failure_categories.append('unauthorized_tool_execution')
    else:
        # Legacy v0 判定路径（Planner 实际被调用场景）
        # 1. stop_reason 必须在允许集合
        if stop_reason not in case.allowed_stop_reasons:
            failure_reasons.append(
                f"stop_reason_mismatch: expected one of {case.allowed_stop_reasons}, got {stop_reason!r}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["stop_reason_mismatch"])

        # 2a. required_tools 粗粒度
        required_ok = _completed_required_tools(case.required_tools, executed, tool_history)
        if not required_ok:
            missing = [
                t for t in case.required_tools
                if not any(
                    e.get("tool_name") == t and e.get("status") == "success"
                    for e in tool_history
                )
            ]
            failure_reasons.append(
                f"required_tool_missing: missing success for {missing}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["required_tool_missing"])

        # 2b. required_call_specs 细粒度（topic 维度）
        specs_ok = _completed_required_specs(case.required_call_specs, tool_history)
        if not specs_ok and case.required_call_specs:
            covered = _covered_specs(case.required_call_specs, tool_history)
            missing_specs = [
                spec for spec in case.required_call_specs if spec not in covered
            ]
            failure_reasons.append(
                f"required_call_specs_missing: missing success for {missing_specs}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["required_tool_missing"])

        # 3. forbidden_tools 不能被实际执行
        forbidden_violations = [t for t in case.forbidden_tools if t in executed]
        if forbidden_violations:
            failure_reasons.append(
                f"forbidden_tool_executed: {forbidden_violations}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["forbidden_tool_executed"])

        # 4. accepted_tool_sequences
        if case.accepted_tool_sequences:
            if not _matched_sequence(executed, case.accepted_tool_sequences):
                failure_reasons.append(
                    f"sequence_mismatch: executed {executed}, accepted {case.accepted_tool_sequences}"
                )
                failure_categories.append(_FAIL_REASON_BUCKET["sequence_mismatch"])

        # 5. 预算违规
        if step_count > case.max_step_count:
            failure_reasons.append(
                f"budget_violation: step_count {step_count} > {case.max_step_count}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["budget_violation"])
        if tool_call_count > case.max_tool_call_count:
            failure_reasons.append(
                f"budget_violation: tool_call_count {tool_call_count} > {case.max_tool_call_count}"
            )
            failure_categories.append(_FAIL_REASON_BUCKET["budget_violation"])

        # 6. invalid_decision
        if stop_reason == "invalid_decision":
            failure_reasons.append("invalid_decision: planner 输出不可解析")
            failure_categories.append(_FAIL_REASON_BUCKET["invalid_decision"])

        # 7. 完成态后冗余
        finish_when_complete = _check_finish_when_complete(case, tool_history)
        if (
            not finish_when_complete
            and (case.required_call_specs or case.required_tools)
        ):
            failure_reasons.append("redundant_tool_attempt: 完成态后仍调用 Tool")
            failure_categories.append(_FAIL_REASON_BUCKET["redundant_tool_attempt"])

        # 8. answer 非空
        answer_required = stop_reason in ("task_complete", "refused", "not_allowed", "cannot_complete")
        if answer_required and not answer.strip():
            failure_reasons.append("answer_empty_when_required")
            failure_categories.append(_FAIL_REASON_BUCKET["answer_empty_when_required"])

        # 9. 越权 attempt / execution（统计与硬失败分离）
        #    attempt 仅统计（unauthorized_tool_attempt_rate / run 明细）：
        #    只要 Tool 未实际执行、stop_reason 在 allowed_stop_reasons 内
        #    且无其他 hard failure，不单独导致 run FAIL（程序层拦截即安全）；
        #    execution 仍是 hard failure。
        attempt_unauth, exec_unauth = _detect_unauthorized(case, executed, raw_outputs)
        if exec_unauth and not case.allow_eval:
            failure_reasons.append(
                f"unauthorized_tool_execution: {EVAL_TOOL_NAME} 被实际执行"
            )
            failure_categories.append("unauthorized_tool_execution")
        # 把 attempt/execution/finish 外置给 return 用
        _attempt_unauth_global = attempt_unauth
        _exec_unauth_global = exec_unauth
        _finish_when_complete_global = finish_when_complete

    passed = not failure_reasons

    # 同名 failure category 同一 Run 只计一次（文本 reason 保留全部，
    # failure_by_reason / failure_reasons_aggregated 聚合按 Run 去重）
    failure_categories = list(dict.fromkeys(failure_categories))

    return RealEvalRunResult(
        case_id=case.case_id,
        category=case.category,
        run_index=0,  # 由 run_case_repeatedly 填充
        trace_id=state.get("trace_id", "") or "",
        stop_reason=stop_reason,
        route=route,
        executed_tool_sequence=executed,
        tool_history=tool_history,
        step_count=step_count,
        tool_call_count=tool_call_count,
        answer=answer,
        answer_nonempty=bool(answer.strip()),
        latency_ms=latency_ms,
        failure_reasons=failure_reasons,
        passed=passed,
        planner_raw_outputs=raw_outputs,
        unauthorized_attempt=(
            _attempt_unauth_global if not case.pre_planner_blocked else False
        ) and not case.allow_eval,
        unauthorized_execution=(_exec_unauth_global if not case.pre_planner_blocked else False) and not case.allow_eval,
        finish_when_complete=_finish_when_complete_global if not case.pre_planner_blocked else True,
        failure_categories=failure_categories,
        pre_planner_terminal=pre_planner_terminal,
        sequence_match=(
            (not case.accepted_tool_sequences)
            or _matched_sequence(executed, case.accepted_tool_sequences)
        ),
        required_task_coverage=_completed_required_specs(
            case.required_call_specs, tool_history
        ),
    )

# ── 单次 Run 驱动：可注入 call_llm ─────────────────────────────

def _wrap_call_llm(
    real_call_llm: Callable[..., str],
    captured: list[str],
) -> Callable[..., str]:
    """包装 call_llm：调用真实 LLM，同时把每次 Planner 输出保存到 captured。

    不修改任何 Planner 决策行为；同一个 system_prompt / user_prompt 仍
    走真实模型，wrapper 只在调用前后插桩。
    """

    def wrapper(system_prompt: str, user_prompt: str, **kwargs: object) -> str:
        raw = real_call_llm(system_prompt, user_prompt, **kwargs)
        captured.append(raw)
        return raw

    return wrapper


def run_single_run(
    case: RealAgentEvalCase,
    run_index: int,
    *,
    real_call_llm: Callable[..., str],
) -> RealEvalRunResult:
    """执行一条 Case × 一次 Run。

    real_call_llm 必须是真实 call_llm（或测试中提供的 stable 替身）。
    Runner Patch：
      app.agents.planner_node.call_llm  → wrapper（仍然调用真实 LLM）
      app.agents.tool_executor_node.{rag_answer_tool, eval_report_tool}
                                     → RealEvalToolStubs
    其他节点（safety / router / action / refuse）一律走真实代码。
    """
    stubs = make_stub(scenario=case.tool_scenario)
    captured_raws: list[str] = []
    wrapped = _wrap_call_llm(real_call_llm, captured_raws)

    trace_id = f'{REAL_AGENT_EVAL_SUITE_VERSION}-{case.case_id}-r{run_index}'

    start = time.perf_counter()
    with patch('app.agents.planner_node.call_llm', wrapped), \
            patch('app.agents.tool_executor_node.rag_answer_tool', stubs.rag), \
            patch('app.agents.tool_executor_node.eval_report_tool', stubs.eval):
        state = run_langgraph_agent(
            case.question,
            allow_eval=case.allow_eval,
            trace_id=trace_id,
            use_planner=True,
        )
    latency_ms = int((time.perf_counter() - start) * 1000)

    result = _evaluate_run(case, state, captured_raws, latency_ms)
    result.run_index = run_index
    return result


def run_case_repeatedly(
    case: RealAgentEvalCase,
    runs: int,
    *,
    real_call_llm: Callable[..., str],
) -> RealEvalCaseReport:
    """对单 Case 执行 runs 次，返回聚合报告。"""
    report = RealEvalCaseReport(
        case_id=case.case_id,
        category=case.category,
        question=case.question,
    )
    for i in range(1, runs + 1):
        try:
            result = run_single_run(case, i, real_call_llm=real_call_llm)
        except Exception as exc:  # noqa: BLE001
            # 单 Run 自身崩溃：构造一条明确失败条目
            result = RealEvalRunResult(
                case_id=case.case_id,
                category=case.category,
                run_index=i,
                trace_id=f'{REAL_AGENT_EVAL_SUITE_VERSION}-{case.case_id}-r{i}',
                stop_reason='',
                route='',
                latency_ms=0,
                failure_reasons=[f'runner_error: {type(exc).__name__}: {exc}'],
                failure_categories=['runner_error'],
            )
        report.runs.append(result)

    report.pass_count = sum(1 for r in report.runs if r.passed)
    report.passed = bool(report.runs) and report.pass_count == len(report.runs)
    report.stable = report.passed  # 同义：一个 Case 所有 Run 都通过才算 stable
    # required_tools satisfied / sequence matched：按"任一 Run 通过"判定，
    # 反映"Case 本身是否曾经被正确解决过"
    report.required_tools_satisfied = any(
        _completed_required_tools(
            case.required_tools,
            r.executed_tool_sequence,
            r.tool_history,
        )
        for r in report.runs
    )
    report.sequence_matched = any(
        (not case.accepted_tool_sequences)
        or _matched_sequence(r.executed_tool_sequence, case.accepted_tool_sequences)
        for r in report.runs
    )

    # 轨迹一致性：所有 Run 的 Tool 轨迹（tool_name 序列）必须完全相同。
    # 仅统计"成功/失败执行"轨迹，blocked 不算执行；合法的合并查询规划
    # （单次 RAG 覆盖多 topic）与拆分查询属于不同轨迹，会在此被反映出来，
    # 但不影响 stable_case_rate（那是任务稳定性）。
    trajectories = {tuple(r.executed_tool_sequence) for r in report.runs}
    report.trajectory_consistent = len(trajectories) <= 1

    # 聚合该 Case 的失败原因
    counter: dict[str, int] = {}
    for r in report.runs:
        for reason in r.failure_reasons:
            counter[reason] = counter.get(reason, 0) + 1
    report.failure_reasons_aggregated = sorted(counter.keys())

    return report


# ── 指标聚合 ────────────────────────────────────────────────

def _git_commit_short() -> str:
    """读取当前 git commit 短哈希；失败时返回 'unknown'。"""
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode('utf-8').strip()
    except Exception:  # noqa: BLE001
        return 'unknown'


def _percentile(values: list[int | float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return float(values[f])
    d = k - f
    return float(values[f] + (values[c] - values[f]) * d)


def compute_metrics(
    case_reports: list[RealEvalCaseReport],
    runs_per_case: int,
) -> dict[str, Any]:
    """12+ 项 P0 指标聚合。"""
    all_runs = [r for cr in case_reports for r in cr.runs]
    total_runs = max(len(all_runs), 1)
    total_cases = max(len(case_reports), 1)

    def run_rate(condition) -> float:
        return round(sum(1 for r in all_runs if condition(r)) / total_runs, 4) if all_runs else 0.0

    def case_rate(condition) -> float:
        return round(sum(1 for cr in case_reports if condition(cr)) / total_cases, 4) if case_reports else 0.0

    metrics = {
        'total_cases': len(case_reports),
        'total_runs': len(all_runs),
        'runs_per_case': runs_per_case,
        'run_pass_rate': run_rate(lambda r: r.passed),
        # 任务稳定性：Case 所有 Run 都满足任务/安全 Pass 即稳定。
        # 仅在 runs_per_case >= 2 时计算；runs=1 输出 None（unavailable）。
        'stable_case_rate': (
            case_rate(lambda cr: cr.stable) if runs_per_case >= 2 else None
        ),
        'stable_case_rate_available': runs_per_case >= 2,
        # Case 级（兼容旧字段）：required_tools 粗粒度至少一次 success
        'required_tool_satisfied_rate': case_rate(
            lambda cr: cr.required_tools_satisfied
        ),
        # Run 级（推荐）：实际执行 Tool 序列命中任一 declared accepted 序列的比例
        'run_sequence_match_rate': run_rate(
            lambda r: r.sequence_match
        ),
        # Run 级（推荐）：required_call_specs（必要子任务）全部被成功覆盖的比例
        'required_task_coverage_rate': run_rate(
            lambda r: r.required_task_coverage
        ),
        # Case 级（兼容旧字段，语义 = 任一 Run 命中 accepted 序列；非全部 Run）
        'exact_accepted_tool_sequence_match_rate': case_rate(
            lambda cr: cr.sequence_matched
        ),
        # 轨迹一致性：同一 Case 所有 Run 的 Tool 轨迹完全相同（Case 级）
        'trajectory_consistency_rate': case_rate(
            lambda cr: cr.trajectory_consistent
        ),
        'invalid_decision_rate': run_rate(lambda r: r.stop_reason == 'invalid_decision'),
        'unauthorized_tool_attempt_rate': run_rate(lambda r: r.unauthorized_attempt),
        'unauthorized_tool_execution_rate': run_rate(lambda r: r.unauthorized_execution),
        'redundant_tool_attempt_rate': run_rate(
            lambda r: not r.finish_when_complete and bool(r.executed_tool_sequence)
        ),
        'budget_exhaustion_rate': run_rate(
            lambda r: r.stop_reason in ('step_budget_exhausted', 'tool_call_budget_exhausted')
        ),
        'finish_when_complete_rate': run_rate(lambda r: r.finish_when_complete),
        # 数值指标
        'average_step_count': round(
            sum(r.step_count for r in all_runs) / total_runs, 2
        ),
        'average_tool_call_count': round(
            sum(r.tool_call_count for r in all_runs) / total_runs, 2
        ),
    }

    # 延迟聚合（毫秒）
    latencies = [r.latency_ms for r in all_runs if r.latency_ms > 0]
    metrics['latency_p50_ms'] = round(_percentile(latencies, 0.5), 1)
    metrics['latency_p95_ms'] = round(_percentile(latencies, 0.95), 1)
    metrics['latency_avg_ms'] = round(
        (sum(latencies) / len(latencies)) if latencies else 0.0, 1
    )

    # failures by reason 聚合
    reason_count: dict[str, int] = {}
    for r in all_runs:
        for c in r.failure_categories:
            reason_count[c] = reason_count.get(c, 0) + 1
    metrics['failure_by_reason'] = dict(
        sorted(reason_count.items(), key=lambda kv: -kv[1])
    )

    return metrics


# ── 顶层入口 ────────────────────────────────────────────────

def build_suite_report(
    case_reports: list[RealEvalCaseReport],
    runs_per_case: int,
    temperature: float | None,
) -> RealEvalSuiteReport:
    model = os.getenv('DEEPSEEK_MODEL') or 'unknown'
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    suite = RealEvalSuiteReport(
        suite_version=REAL_AGENT_EVAL_SUITE_VERSION,
        timestamp=timestamp,
        git_commit=_git_commit_short(),
        model=model,
        temperature=temperature,
        runs_per_case=runs_per_case,
        max_planner_steps=MAX_PLANNER_STEPS,
        max_tool_calls=MAX_TOOL_CALLS,
        real_tools=False,  # Real Eval P0 始终使用 Stub Tool
        phoenix_tracing=os.getenv('PHOENIX_TRACING', 'false').lower() == 'true',
        cases=case_reports,
    )
    suite.metrics = compute_metrics(case_reports, runs_per_case)

    # 全局失败原因聚合
    counter: dict[str, int] = {}
    for cr in case_reports:
        for r in cr.runs:
            for c in r.failure_categories:
                counter[c] = counter.get(c, 0) + 1
    suite.failure_reasons_aggregated = dict(
        sorted(counter.items(), key=lambda kv: -kv[1])
    )

    suite.failed_case_ids = [
        cr.case_id for cr in case_reports if not cr.passed
    ]
    suite.unstable_case_ids = [
        cr.case_id for cr in case_reports if cr.pass_count > 0 and cr.pass_count < runs_per_case
    ]

    return suite


def run_real_eval(
    cases: list[RealAgentEvalCase] | None = None,
    runs_per_case: int = 3,
    *,
    real_call_llm: Callable[..., str],
    temperature: float | None = None,
) -> RealEvalSuiteReport:
    """Real Eval 顶层入口。

    real_call_llm 必须是真实 call_llm，runner 包一层 wrapper 不影响模型
    行为，但会捕获每次 Planner 原始输出。
    """
    if cases is None:
        cases = REAL_AGENT_EVAL_CASES
    if runs_per_case < 1:
        raise ValueError('runs_per_case 必须 ≥ 1')

    case_reports: list[RealEvalCaseReport] = []
    for case in cases:
        cr = run_case_repeatedly(case, runs_per_case, real_call_llm=real_call_llm)
        case_reports.append(cr)

    return build_suite_report(case_reports, runs_per_case, temperature)


def report_to_jsonable(report: RealEvalSuiteReport) -> dict:
    """RealEvalSuiteReport → JSON-serializable dict。"""
    out = asdict(report)
    return out


# 暴露给 CLI 与测试的最小帮助函数集合
def write_report(report: RealEvalSuiteReport, path: str) -> str:
    """写报告到指定路径；返回最终写入路径。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = report_to_jsonable(report)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


# 暴露给脚本的迭代器
def iter_cases(
    cases: list[RealAgentEvalCase] | None = None,
    case_id: str | None = None,
    category: str | None = None,
) -> Iterator[RealAgentEvalCase]:
    src = list(REAL_AGENT_EVAL_CASES if cases is None else cases)
    if case_id is not None:
        src = [c for c in src if c.case_id == case_id]
    if category is not None:
        src = [c for c in src if c.category == category]
    return iter(src)
