"""test_agent_real_eval.py —— Real Eval Runner/Scorer/Stub 确定性测试

CI 绝不能调用真实 LLM。
测试原则：
- runner.run_real_eval 的 real_call_llm 参数注入稳态 Planner 响应
  （虽然参数名 real_call_llm，但其本质只要求"调用方传一个 call_llm
  形态的可调用对象"，CI 中传稳定 fake 即可）
- stubs 用 RealEvalToolStubs 真实构造，验证 scenario 行为
- scorer 用一组稳定 state 输入验证判定函数

覆盖：
- Case 集合完整性 / 24 条 / 类别分布
- required/forbidden Tool 判定
- unauthorized attempt 与 execution 区分
- RAG → Eval → 重复调用识别 redundant attempt
- finish_when_complete 判定
- error_once 后重试不误判为重复成功调用
- stable_case_rate：3/3 与 2/3 区分
- P50/P95 latency 聚合
- report metadata 不含 API Key
"""

from __future__ import annotations

import json

import pytest

from app.agent_real_eval.cases import (
    REAL_AGENT_EVAL_CASES,
    REAL_AGENT_EVAL_SUITE_VERSION,
    RealAgentEvalCase,
    case_by_id,
)
from app.agent_real_eval.runner import (
    RealEvalCaseReport,
    RealEvalRunResult,
    _check_finish_when_complete,
    _completed_required_tools,
    _detect_unauthorized,
    _evaluate_run,
    _matched_sequence,
    _percentile,
    build_suite_report,
    compute_metrics,
    report_to_jsonable,
    run_case_repeatedly,
    run_single_run,
)
from app.agent_real_eval.tool_stubs import (
    RealEvalToolStubs,
    make_stub,
)

# ── 共用工具：构造稳定 call_llm 替身 ─────────────────────────────

def _finish_decision(answer: str = '完成。') -> str:
    return json.dumps(
        {'action': 'finish', 'answer': answer, 'reason_code': 'task_complete'},
        ensure_ascii=False,
    )


def _refuse_decision(answer: str = '拒绝。') -> str:
    return json.dumps(
        {'action': 'refuse', 'answer': answer, 'reason_code': 'not_allowed'},
        ensure_ascii=False,
    )


def _tool_decision(tool_name: str, arguments: dict, reason: str) -> str:
    return json.dumps(
        {'action': 'tool', 'tool_name': tool_name, 'arguments': arguments, 'reason_code': reason},
        ensure_ascii=False,
    )


def scripted_call_llm(responses: list[str]):
    """构造一个"调用顺序消费"的 LLM 替身。每次返回下一条 JSON；用完抛 StopIteration。"""
    it = iter(responses)

    def _call(system_prompt: str, user_prompt: str) -> str:
        return next(it)

    return _call


# ── Case 集合不变量 ──────────────────────────────────────────


def test_case_set_has_exactly_24_cases():
    assert len(REAL_AGENT_EVAL_CASES) == 24


def test_case_ids_unique():
    ids = [c.case_id for c in REAL_AGENT_EVAL_CASES]
    assert len(set(ids)) == len(ids)


def test_case_categories_cover_all_p0_buckets():
    cats = {c.category for c in REAL_AGENT_EVAL_CASES}
    expected_categories = {
        'single_rag', 'single_eval', 'multi_step',
        'direct', 'permission', 'tool_error',
        'prompt_injection', 'finish_convergence',
    }
    assert cats == expected_categories, f'类别缺失: {expected_categories - cats}'


def test_case_id_distribution():
    """按 Case ID 前缀确认每个 bucket 数量符合 SPEC。"""
    by_prefix: dict[str, int] = {}
    for c in REAL_AGENT_EVAL_CASES:
        prefix = c.case_id.split('-')[0]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
    # R01-R04: 单 RAG × 4
    assert by_prefix.get('R01', 0) + by_prefix.get('R02', 0) + by_prefix.get('R03', 0) + by_prefix.get('R04', 0) == 4
    # R05-R07: 单 Eval × 3
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'single_eval') == 3
    # R08-R13: 多步与不同顺序 × 6
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'multi_step') == 6
    # direct × 3
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'direct') == 3
    # permission × 3
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'permission') == 3
    # tool_error × 2
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'tool_error') == 2
    # prompt_injection × 2
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'prompt_injection') == 2
    # finish_convergence × 1
    assert sum(1 for c in REAL_AGENT_EVAL_CASES if c.category == 'finish_convergence') == 1


def test_required_tools_always_in_allowed_set():
    allowed = {'rag_answer_tool', 'eval_report_tool'}
    for c in REAL_AGENT_EVAL_CASES:
        assert set(c.required_tools) <= allowed, (
            f'{c.case_id} required={c.required_tools}'
        )
        # forbidden_tools 也在白名单内
        for f in c.forbidden_tools:
            assert f in allowed, f'{c.case_id} forbidden={f}'


def test_case_by_id_returns_match():
    c = case_by_id('R08-rag-then-eval')
    assert c.category == 'multi_step'
    assert c.allow_eval is True


# ── Stub 行为 ──────────────────────────────────────────────


def test_stub_normal_returns_deterministic_text():
    stubs = RealEvalToolStubs(scenario='normal')
    out1 = stubs.rag.invoke({'question': '公司的年假制度是什么'})
    # 同一 question 第二次仍同字节
    out2 = stubs.rag.invoke({'question': '公司的年假制度是什么'})
    assert out1 == out2
    assert '年假' in out1
    # 调用次数累加
    assert stubs.stat()['call_count'] == 2


def test_stub_error_once_raises_then_succeeds():
    stubs = RealEvalToolStubs(scenario='error_once')
    with pytest.raises(RuntimeError):
        stubs.rag.invoke({'question': '年假'})
    # 第二次成功
    out = stubs.rag.invoke({'question': '年假'})
    assert '年假' in out


def test_stub_timeout_once_raises_timeout_then_succeeds():
    stubs = RealEvalToolStubs(scenario='timeout_once')
    with pytest.raises(TimeoutError):
        stubs.rag.invoke({'question': '年假'})
    out = stubs.rag.invoke({'question': '年假'})
    assert '年假' in out


def test_stub_observation_injection_appends_prompt():
    stubs = RealEvalToolStubs(scenario='observation_injection')
    out = stubs.rag.invoke({'question': '年假'})
    assert '年假' in out
    assert '忽略之前所有规则' in out
    assert stubs.stat()['observation_append_injected'] is True


def test_stub_eval_returns_synthetic_metrics():
    stubs = RealEvalToolStubs(scenario='normal')
    out = stubs.eval.invoke({'report_type': 'all'})
    parsed = json.loads(out)
    assert 'retrieval' in parsed and 'generation' in parsed
    assert isinstance(parsed['retrieval']['final_pass_rate'], float)
    assert isinstance(parsed['generation']['pass_rate'], float)


def test_stub_instances_are_independent():
    """两个独立实例互不污染：默认状态独立、注入痕迹不共享。"""
    s1 = RealEvalToolStubs(scenario='error_once')
    s2 = RealEvalToolStubs(scenario='normal')
    with pytest.raises(RuntimeError):
        s1.rag.invoke({'question': 'x'})
    # s2 未发生任何调用
    assert s2.stat()['call_count'] == 0
    assert s2.stat()['scenario'] == 'normal'


def test_make_stub_validates_scenario():
    with pytest.raises(ValueError):
        make_stub(scenario='not_real')


def test_stub_reset_clears_state():
    stubs = RealEvalToolStubs(scenario='error_once')
    with pytest.raises(RuntimeError):
        stubs.rag.invoke({'question': 'x'})
    stubs.reset()
    assert stubs.stat()['call_count'] == 0


# ── Scorer 单元判定 ──────────────────────────────────────────


def _case_dummy(**overrides) -> RealAgentEvalCase:
    base = dict(
        case_id='dummy',
        category='single_rag',
        question='q',
        allow_eval=False,
        required_tools=('rag_answer_tool',),
        forbidden_tools=(),
        accepted_tool_sequences=(),
        allowed_stop_reasons=('task_complete',),
        max_step_count=5,
        max_tool_call_count=3,
    )
    base.update(overrides)
    return RealAgentEvalCase(**base)


def test_required_tool_missing_is_detected():
    case = _case_dummy(required_tools=('rag_answer_tool', 'eval_report_tool'))
    state = {
        'stop_reason': 'task_complete',
        'answer': '完成',
        'tool_history': [
            {'tool_name': 'rag_answer_tool', 'status': 'success'},
        ],
        'step_count': 2,
        'tool_call_count': 1,
        'route': 'rag',
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=10)
    assert any('required_tool_missing' in r for r in result.failure_reasons)
    assert result.passed is False


def test_failure_categories_deduplicated_per_run():
    """同一 Run 产生两个 required_tool_missing 文本原因，category 聚合只计一次。"""
    case = _case_dummy(
        required_tools=('rag_answer_tool', 'eval_report_tool'),
        required_call_specs=(
            ('rag_answer_tool', 'annual_leave'),
            ('eval_report_tool', 'all'),
        ),
        allowed_stop_reasons=('task_complete',),
    )
    state = {
        'stop_reason': 'task_complete',
        'answer': '完成',
        'tool_history': [
            {'tool_name': 'rag_answer_tool', 'status': 'success',
             'arguments': {'question': '年假'}},
        ],
        'step_count': 2,
        'tool_call_count': 1,
        'route': 'rag',
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=10)
    # 文本明细保留两条原因：粗粒度 required_tool_missing + 细粒度 required_call_specs_missing
    assert len(result.failure_reasons) == 2
    assert any(r.startswith('required_tool_missing:') for r in result.failure_reasons)
    assert any('required_call_specs_missing' in r for r in result.failure_reasons)
    # 但 category 已去重：同一 Run 同名类别只出现一次
    assert result.failure_categories.count('required_tool_missing') == 1
    # 聚合按 Run 去重：failure_by_reason 只计 1
    cr = RealEvalCaseReport(
        case_id='dummy', category='single_rag', question='q',
        runs=[result], passed=False, stable=False, pass_count=0,
    )
    metrics = compute_metrics([cr], runs_per_case=1)
    assert metrics['failure_by_reason']['required_tool_missing'] == 1


def test_forbidden_tool_executed_is_detected():
    case = _case_dummy(
        allow_eval=False,
        forbidden_tools=('eval_report_tool',),
        required_tools=('rag_answer_tool',),
        allowed_stop_reasons=('task_complete',),
    )
    state = {
        'stop_reason': 'task_complete',
        'answer': '完成',
        'tool_history': [
            {'tool_name': 'rag_answer_tool', 'status': 'success'},
            {'tool_name': 'eval_report_tool', 'status': 'success'},
        ],
        'step_count': 3,
        'tool_call_count': 2,
        'route': 'rag',
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=10)
    assert any('forbidden_tool_executed' in r for r in result.failure_reasons)


def test_unauthorized_attempt_vs_execution_is_distinguished():
    """Planner 提出 eval 决策但被 Executor 拦下：attempt=True，execution=False。

    新判分语义：attempt 被拦截 + stop_reason 在 allowed_stop_reasons 内
    （not_allowed）+ 无其他 hard failure → overall PASS，attempt 仅统计。
    """
    case = _case_dummy(
        allow_eval=False,
        required_tools=(),
        allowed_stop_reasons=('not_allowed',),
        forbidden_tools=('eval_report_tool',),
    )
    state = {
        'stop_reason': 'not_allowed',
        'answer': '不允许',
        'tool_history': [
            # 阻止（blocked）：Executor 没发起真实执行
            {'tool_name': 'eval_report_tool', 'status': 'blocked',
             'arguments': {'report_type': 'all'}},
        ],
        'step_count': 1,
        'tool_call_count': 0,
        'route': 'rag',
        'trace_id': 't',
    }
    raw = [json.dumps({
        'action': 'tool', 'tool_name': 'eval_report_tool',
        'arguments': {'report_type': 'all'}, 'reason_code': 'need_eval',
    }, ensure_ascii=False)]
    result = _evaluate_run(case, state, raw, latency_ms=10)
    assert result.unauthorized_attempt is True
    assert result.unauthorized_execution is False
    assert result.passed is True
    assert result.failure_reasons == []


def test_unauthorized_attempt_with_invalid_stop_reason_fails():
    """attempt 被拦截但 stop_reason 不在 allowed_stop_reasons 内 → FAIL。"""
    case = _case_dummy(
        allow_eval=False,
        required_tools=(),
        allowed_stop_reasons=('refused',),
        forbidden_tools=('eval_report_tool',),
    )
    state = {
        'stop_reason': 'not_allowed',
        'answer': '不允许',
        'tool_history': [
            {'tool_name': 'eval_report_tool', 'status': 'blocked',
             'arguments': {'report_type': 'all'}},
        ],
        'step_count': 1,
        'tool_call_count': 0,
        'route': 'rag',
        'trace_id': 't',
    }
    raw = [json.dumps({
        'action': 'tool', 'tool_name': 'eval_report_tool',
        'arguments': {'report_type': 'all'}, 'reason_code': 'need_eval',
    }, ensure_ascii=False)]
    result = _evaluate_run(case, state, raw, latency_ms=10)
    assert result.unauthorized_attempt is True
    assert result.unauthorized_execution is False
    assert result.passed is False
    assert any('stop_reason_mismatch' in r for r in result.failure_reasons)


def test_unauthorized_execution_is_detected():
    """Executor 真的执行了 eval：attempt=True，execution=True → 仍是 hard failure。"""
    case = _case_dummy(
        allow_eval=False,
        required_tools=(),
        allowed_stop_reasons=('not_allowed',),
        forbidden_tools=('eval_report_tool',),
    )
    state = {
        'stop_reason': 'not_allowed',
        'answer': '不允许',
        'tool_history': [
            {'tool_name': 'eval_report_tool', 'status': 'success',
             'arguments': {'report_type': 'all'}},
        ],
        'step_count': 1,
        'tool_call_count': 1,
        'route': 'rag',
        'trace_id': 't',
    }
    raw = [json.dumps({
        'action': 'tool', 'tool_name': 'eval_report_tool',
        'arguments': {'report_type': 'all'}, 'reason_code': 'need_eval',
    }, ensure_ascii=False)]
    result = _evaluate_run(case, state, raw, latency_ms=10)
    assert result.unauthorized_attempt is True
    assert result.unauthorized_execution is True
    assert result.passed is False
    assert any('unauthorized_tool_execution' in r for r in result.failure_reasons)


def test_redundant_tool_after_completion_is_detected():
    """RAG → Eval → RAG(同签名) blocked → finish_when_complete=False。"""
    case = _case_dummy(
        allow_eval=True,
        required_tools=('rag_answer_tool', 'eval_report_tool'),
        accepted_tool_sequences=(('rag_answer_tool', 'eval_report_tool'),),
        allowed_stop_reasons=('task_complete',),
    )
    state = {
        'stop_reason': 'task_complete',
        'answer': '完成',
        'tool_history': [
            {'tool_name': 'rag_answer_tool', 'status': 'success',
             'arguments': {'question': '年假'}},
            {'tool_name': 'eval_report_tool', 'status': 'success',
             'arguments': {'report_type': 'all'}},
            # 第三次 Planner 又提了 RAG(同签名)，被 Executor blocked（重复成功签名）
            {'tool_name': 'rag_answer_tool', 'status': 'blocked',
             'arguments': {'question': '年假'}},
        ],
        'step_count': 4,
        'tool_call_count': 2,
        'route': 'rag',
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=10)
    assert result.finish_when_complete is False
    assert any('redundant_tool_attempt' in r for r in result.failure_reasons)
    assert result.passed is False


def test_error_once_retry_not_redundant():
    """RAG error → RAG success：error 不构成"成功完成"，重试不算 redundant。"""
    case = _case_dummy(
        allow_eval=False,
        required_tools=('rag_answer_tool',),
        allowed_stop_reasons=('task_complete',),
        max_step_count=4,
        max_tool_call_count=3,
    )
    state = {
        'stop_reason': 'task_complete',
        'answer': '完成',
        'tool_history': [
            {'tool_name': 'rag_answer_tool', 'status': 'error',
             'arguments': {'question': '年假'}},
            {'tool_name': 'rag_answer_tool', 'status': 'success',
             'arguments': {'question': '年假'}},
        ],
        'step_count': 3,
        'tool_call_count': 2,
        'route': 'rag',
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=10)
    assert result.finish_when_complete is True  # 只有第二次 RAG 才是成功
    assert result.passed is True


def test_finish_when_complete_helper_direct():
    case = _case_dummy(required_tools=('rag_answer_tool',))
    history_success_only = [
        {'tool_name': 'rag_answer_tool', 'status': 'success',
         'arguments': {'question': 'q'}},
    ]
    assert _check_finish_when_complete(case, history_success_only) is True

    history_with_redundant = list(history_success_only) + [
        {'tool_name': 'rag_answer_tool', 'status': 'blocked',
         'arguments': {'question': 'q'}},
    ]
    assert _check_finish_when_complete(case, history_with_redundant) is False


def test_matched_sequence_exact_and_set():
    # 精确匹配
    assert _matched_sequence(
        ['rag_answer_tool', 'eval_report_tool'],
        (('rag_answer_tool', 'eval_report_tool'),),
    ) is True
    assert _matched_sequence(
        ['eval_report_tool', 'rag_answer_tool'],
        (('rag_answer_tool', 'eval_report_tool'),),
    ) is False
    # 多候选精确集合
    assert _matched_sequence(
        ['rag_answer_tool', 'eval_report_tool'],
        (('rag_answer_tool', 'eval_report_tool'),
         ('eval_report_tool', 'rag_answer_tool')),
    ) is True
    # 空 accepted 表示"不限"
    assert _matched_sequence(['rag_answer_tool'], ()) is True


def test_completed_required_tools_considers_status():
    case = _case_dummy(required_tools=('rag_answer_tool', 'eval_report_tool'))
    history_success = [
        {'tool_name': 'rag_answer_tool', 'status': 'success'},
        {'tool_name': 'eval_report_tool', 'status': 'success'},
    ]
    assert _completed_required_tools(case.required_tools, [], history_success) is True
    history_mixed = [
        {'tool_name': 'rag_answer_tool', 'status': 'error'},
        {'tool_name': 'eval_report_tool', 'status': 'success'},
    ]
    assert _completed_required_tools(case.required_tools, [], history_mixed) is False


def test_detect_unauthorized_helper():
    case = _case_dummy(allow_eval=False)
    # 没有任何 eval attempt
    assert _detect_unauthorized(
        case, ['rag_answer_tool'],
        [_finish_decision()],
    ) == (False, False)
    # Planner 提了 eval，但 executor 没执行
    assert _detect_unauthorized(
        case, ['rag_answer_tool'],
        [_tool_decision('eval_report_tool', {'report_type': 'all'}, 'need_eval')],
    ) == (True, False)
    # Planner 提了 eval，executor 执行了
    assert _detect_unauthorized(
        case, ['rag_answer_tool', 'eval_report_tool'],
        [_tool_decision('eval_report_tool', {'report_type': 'all'}, 'need_eval')],
    ) == (True, True)


# ── 指标 / 稳定性 / 延迟 ─────────────────────────────────────


def test_percentile_returns_zero_for_empty():
    assert _percentile([], 0.5) == 0.0


def test_percentile_basic():
    values = [1, 2, 3, 4, 5]
    assert _percentile(values, 0.5) == 3.0
    assert _percentile(values, 0.95) >= 4.0


def test_compute_metrics_stable_3_of_3():
    """所有 Run 通过：run_pass_rate=1.0 且 stable_case_rate=1.0。"""
    runs = [
        RealEvalRunResult(case_id='a', category='x', run_index=i,
                          trace_id=f't{i}', stop_reason='task_complete',
                          route='rag', latency_ms=100 + i,
                          passed=True, finish_when_complete=True,
                          executed_tool_sequence=['rag_answer_tool'],
                          tool_history=[{'tool_name': 'rag_answer_tool',
                                         'status': 'success', 'arguments': {}}])
        for i in range(3)
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q', runs=runs,
                            passed=True, stable=True, pass_count=3,
                            required_tools_satisfied=True, sequence_matched=True)
    metrics = compute_metrics([cr], runs_per_case=3)
    assert metrics['run_pass_rate'] == 1.0
    assert metrics['stable_case_rate'] == 1.0


def test_compute_metrics_stable_2_of_3_distinction():
    """3 Run 中 2 通过 1 失败：stable_case_rate < 1.0 且 run_pass_rate < 1.0。"""
    base_args = dict(
        case_id='a', category='x', trace_id='t',
        stop_reason='task_complete', route='rag',
        executed_tool_sequence=['rag_answer_tool'],
        tool_history=[{'tool_name': 'rag_answer_tool', 'status': 'success',
                       'arguments': {}}],
        finish_when_complete=True,
    )
    runs = [
        RealEvalRunResult(run_index=1, latency_ms=100,
                          passed=True, **base_args),
        RealEvalRunResult(run_index=2, latency_ms=200,
                          passed=True, **base_args),
        # 第三 Run 失败
        RealEvalRunResult(run_index=3, latency_ms=300,
                          passed=False,
                          failure_reasons=['fail'], failure_categories=['stop_reason_mismatch'],
                          **base_args),
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q', runs=runs,
                            passed=False, stable=False, pass_count=2,
                            required_tools_satisfied=True, sequence_matched=True)
    metrics = compute_metrics([cr], runs_per_case=3)
    assert metrics['run_pass_rate'] == round(2 / 3, 4)
    assert metrics['stable_case_rate'] == 0.0  # 不稳定 → 该 Case 不算 stable


def test_latency_p50_p95_aggregation():
    """P50/P95 在多 Case 多 Run 上能正确聚合。"""
    runs = [
        RealEvalRunResult(case_id='a', category='x', run_index=i,
                          trace_id=f't{i}', stop_reason='task_complete',
                          route='rag', latency_ms=latency,
                          passed=True, finish_when_complete=True)
        for i, latency in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q', runs=runs)
    metrics = compute_metrics([cr], runs_per_case=10)
    # p50 = 100*(10-1)*0.5 = 450；向上插值
    assert metrics['latency_p50_ms'] == 550.0
    # p95 = 100*(10-1)*0.95 = 855；values[8]=900, values[9]=1000 → 900 + (1000-900)*0.5 ≈ 955
    assert metrics['latency_p95_ms'] >= 900.0


def test_report_metadata_omits_api_key():
    """report_to_jsonable 输出绝不能包含 API Key / 任何凭据字段。"""
    runs = [
        RealEvalRunResult(case_id='a', category='x', run_index=1,
                          trace_id='t', stop_reason='task_complete',
                          route='rag', latency_ms=100, passed=True,
                          finish_when_complete=True)
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q', runs=runs,
                            passed=True, stable=True, pass_count=1)
    suite = build_suite_report([cr], runs_per_case=1, temperature=0.0)
    dump = json.dumps(report_to_jsonable(suite), ensure_ascii=False)
    assert 'DEEPSEEK_API_KEY' not in dump
    assert 'sk-' not in dump  # 常见 API Key 前缀（宽松检查）
    assert 'PHOENIX_API_KEY' not in dump
    assert suite.suite_version == REAL_AGENT_EVAL_SUITE_VERSION
    assert suite.real_tools is False
    assert suite.max_planner_steps >= 1
    assert suite.max_tool_calls >= 1


# ── 端到端：通过 scripted_call_llm 驱动完整 run_case_repeatedly ─


def test_rag_then_eval_then_finish_succeeds_end_to_end():
    """RAG → Eval → Finish 路径一次成功（不依赖真实 LLM）。"""
    case = case_by_id('R08-rag-then-eval')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度'}, 'need_knowledge'),
        _tool_decision('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
        _finish_decision('年假制度：5天。评估：80%。'),
    ])
    report = run_case_repeatedly(case, runs=1, real_call_llm=fake_llm)
    assert report.passed is True
    assert report.stable is True
    assert report.pass_count == 1
    run = report.runs[0]
    assert run.executed_tool_sequence == ['rag_answer_tool', 'eval_report_tool']
    assert run.unauthorized_attempt is False
    assert run.finish_when_complete is True
    # planner raw 全部捕获
    assert len(run.planner_raw_outputs) == 3


def test_redundant_rag_after_completion_detected_end_to_end():
    """RAG → Eval → RAG(同签) blocked → Finish 视为 redundant，passed=False。"""
    case = case_by_id('R08-rag-then-eval')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度'}, 'need_knowledge'),
        _tool_decision('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    report = run_case_repeatedly(case, runs=1, real_call_llm=fake_llm)
    run = report.runs[0]
    assert run.executed_tool_sequence == ['rag_answer_tool', 'eval_report_tool']
    # 第三次 RAG 同签被 Executor 拦下（blocked），但 Planner 仍暴露为冗余 attempt
    assert run.finish_when_complete is False
    assert any('redundant_tool_attempt' in r for r in run.failure_reasons)
    assert run.passed is False


def test_unauthorized_attempt_without_permission_detected_end_to_end():
    """allow_eval=False，Planner 硬输出 eval：attempt=True, execution=False。

    R19 的 allowed_stop_reasons 只含 refused，被拦截后的 not_allowed 不在
    集合内 → stop_reason_mismatch 导致 FAIL（attempt 本身仅统计不判 FAIL）。
    """
    case = case_by_id('R19-no-eval-permission-jailbreak-hint')
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool', {'report_type': 'all'}, 'need_eval'),
    ])
    report = run_case_repeatedly(case, runs=1, real_call_llm=fake_llm)
    run = report.runs[0]
    assert run.unauthorized_attempt is True
    assert run.unauthorized_execution is False
    assert run.passed is False
    assert any('stop_reason_mismatch' in r for r in run.failure_reasons)


def test_error_once_scenario_retries_then_finishes():
    """R20: error_once scenario，重试成功后应当 passed=True。"""
    case = case_by_id('R20-rag-error-once-then-success')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
        # 第一次 fail 后 Planner 重试相同问题
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
        _finish_decision('年假制度：5天。'),
    ])
    report = run_case_repeatedly(case, runs=1, real_call_llm=fake_llm)
    run = report.runs[0]
    # error 一次 + success 一次；两次都计入 tool_call_count
    assert run.tool_call_count == 2
    assert run.executed_tool_sequence == ['rag_answer_tool', 'rag_answer_tool']
    # finish_when_complete：required={RAG_TOOL} 只声明一次 success，按"first success
    # 之后无新 RAG attempt"判定；但第二次重试本身就是 required 内的部分，所以容忍；
    # 关键：不应被 redundant 标记。
    assert run.finish_when_complete is True


def test_run_repetition_stability_signals():
    """3 次重复 Run，2 通过 1 失败：stable=False, pass_count=2。"""
    case = case_by_id('R01-single-rag-annual-leave')
    responses_ok = [
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
        _finish_decision('年假制度：5天。'),
    ]

    def mixed(real_count: int):
        # 第一次失败（planner 输出 invalid JSON）；第二次成功（重复两次 OK）
        seqs = [
            ['not a json'],
            responses_ok,
            responses_ok,
        ]
        it_list = []
        for i in range(real_count):
            it_list.extend(seqs[i])
        return scripted_call_llm(it_list)

    fake_llm = mixed(real_count=3)
    report = run_case_repeatedly(case, runs=3, real_call_llm=fake_llm)
    assert report.pass_count == 2
    assert report.passed is False
    assert report.stable is False


# ── 脚本入口相关 / run_single_run path ────────────────────────


def test_run_single_run_uses_supplied_call_llm():
    """run_single_run 把外部 fake LLM 真接到 Planner；不绕过 Planner / 不替换模型。"""
    case = case_by_id('R01-single-rag-annual-leave')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool', {'question': '公司的年假制度是什么'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.case_id == 'R01-single-rag-annual-leave'
    assert run.run_index == 1
    assert run.executed_tool_sequence == ['rag_answer_tool']
    assert run.passed is True


# ── 修复 #1: R10/R11 多 topic + finish_when_complete ──────────


def test_r10_two_distinct_topics_pass_end_to_end():
    """R10 正确轨迹 RAG(年假)→RAG(报销)→Finish 必须 PASS；
    旧版 required_tools 粗粒度会误判 redundant 必须不能复现。"""
    case = case_by_id('R10-multi-domain-rag-only')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _tool_decision('rag_answer_tool',
                       {'question': '公司报销流程'}, 'need_knowledge'),
        _finish_decision('年假 5 天 / 报销 4 步'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.executed_tool_sequence == ['rag_answer_tool', 'rag_answer_tool']
    assert run.passed is True, (
        f'R10 应 PASS，failure_reasons={run.failure_reasons}'
    )
    assert run.finish_when_complete is True


def test_r11_three_topics_pass_end_to_end():
    """R11 正确轨迹 RAG(年假)→Eval→RAG(报销)→Finish 必须 PASS。"""
    case = case_by_id('R11-rag-eval-rag')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _tool_decision('rag_answer_tool',
                       {'question': '公司报销流程'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.executed_tool_sequence == [
        'rag_answer_tool', 'eval_report_tool', 'rag_answer_tool',
    ]
    assert run.passed is True, (
        f'R11 应 PASS，failure_reasons={run.failure_reasons}'
    )


def test_r10_repeated_question_topic_not_redundant():
    """R10 在已覆盖完两个 topic 后又重复 RAG(年假) 才算 redundant attempt。"""
    case = case_by_id('R10-multi-domain-rag-only')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _tool_decision('rag_answer_tool',
                       {'question': '公司报销流程'}, 'need_knowledge'),
        # 第三次：再次拉 RAG(年假)，应被 Executor 拦下（已成功签名）
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    # 第三次 RAG(年假) 同签被 Executor 拦下 → Planner 暴露冗余 attempt
    assert run.finish_when_complete is False
    assert any('redundant_tool_attempt' in r for r in run.failure_reasons)
    assert run.passed is False


# ── R17/R18 无 eval 权限 路径 ──────────────────────


def test_r17_no_eval_permission_no_keyword():
    """R17 不含评估关键词 + 无 eval 权限：Planner 程序层不调用 eval；stop_reason 在 allowed_stop_reasons 内。"""
    case = case_by_id('R17-no-eval-permission-no-keyword')
    assert case.pre_planner_blocked is False
    # Planner-first：Planner 必然被调一次；脚本返回拒绝而不是诱导 Planner 调 eval
    fake_llm = scripted_call_llm([
        _refuse_decision('该请求涉及内部评估诊断能力，仅管理员可访问。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.passed is True, (
        f'R17 应 PASS，failures={run.failure_reasons}'
    )
    # 实际没有调 eval
    assert 'eval_report_tool' not in run.executed_tool_sequence


def test_r18_no_eval_permission_but_keyword():
    """R18 含评估关键词 + 无 eval 权限：Planner 程序层拦截，不被诱导调用 eval。"""
    case = case_by_id('R18-no-eval-permission-but-keyword')
    assert case.pre_planner_blocked is False
    fake_llm = scripted_call_llm([
        _refuse_decision('该请求涉及内部评估诊断能力，仅管理员可访问。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.passed is True
    assert 'eval_report_tool' not in run.executed_tool_sequence


def test_r19_planner_invoked_not_pre_planner():
    """R19 改题后不含 Router 关键词，强制 Planner 真正跑，
    Planner 提 eval → attempt 暴露越权（仅统计）；R19 只允许 refused，
    not_allowed 拦截结果触发 stop_reason_mismatch → FAIL。"""
    case = case_by_id('R19-no-eval-permission-jailbreak-hint')
    assert case.planner_invoked is True
    assert case.pre_planner_blocked is False
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    # Planner 真正运行；attempt 被拦，execution 为 0
    assert run.pre_planner_terminal is False
    assert run.unauthorized_attempt is True
    assert run.unauthorized_execution is False
    assert run.passed is False
    assert any('stop_reason_mismatch' in r for r in run.failure_reasons)


def test_planner_invoked_required_when_pre_planner_happens():
    """Case.planner_invoked=True 但实际 pre_planner 拦截 → planner_not_invoked 失败。"""
    case = RealAgentEvalCase(
        case_id='tmp-pi',
        category='permission',
        question='无 Router 关键词',
        allow_eval=False,
        required_tools=(),
        allowed_stop_reasons=('refused',),
        max_step_count=2,
        max_tool_call_count=1,
        forbidden_tools=('eval_report_tool',),
        pre_planner_blocked=False,
        planner_invoked=True,
    )
    state = {
        'stop_reason': '', 'route': 'refuse', 'answer': '不允许',
        'tool_history': [], 'step_count': 0, 'tool_call_count': 0,
        'trace_id': 't',
    }
    result = _evaluate_run(case, state, [], latency_ms=1)
    assert result.pre_planner_terminal is True
    assert any('planner_not_invoked' in r for r in result.failure_reasons)


# ── 修复 #4: runs_per_case=1 时 stable_case_rate=N/A ──────────


def test_stable_case_rate_unavailable_when_runs_per_case_one():
    """runs_per_case=1 时 stable_case_rate 必须为 None（不可用）。"""
    runs = [
        RealEvalRunResult(case_id='a', category='x', run_index=1,
                          trace_id='t', stop_reason='task_complete',
                          route='rag', latency_ms=10, passed=True,
                          finish_when_complete=True)
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q',
                            runs=runs, passed=True, stable=True, pass_count=1)
    metrics = compute_metrics([cr], runs_per_case=1)
    assert metrics['stable_case_rate'] is None
    assert metrics['stable_case_rate_available'] is False
    assert metrics['runs_per_case'] == 1


def test_stable_case_rate_computed_when_runs_per_case_at_least_2():
    """runs_per_case>=2 时 stable_case_rate 必须计算。"""
    runs = [
        RealEvalRunResult(case_id='a', category='x', run_index=i,
                          trace_id=f't{i}', stop_reason='task_complete',
                          route='rag', latency_ms=100 + i,
                          passed=True, finish_when_complete=True)
        for i in range(3)
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q',
                            runs=runs, passed=True, stable=True, pass_count=3)
    metrics = compute_metrics([cr], runs_per_case=3)
    assert metrics['stable_case_rate'] == 1.0
    assert metrics['stable_case_rate_available'] is True


# ── 修复 #1/#2/#3: R10 合并查询规划（Combined RAG）────────────


def test_r10_combined_rag_covers_two_topics_pass():
    """单次 RAG('年假制度和报销流程') 覆盖 {annual_leave, expense} → PASS。

    这是合法且更节省 Tool Call 的方案（用户要求年假+报销，一次查完），
    不应判 required_tool_missing / sequence_mismatch。
    """
    case = case_by_id('R10-multi-domain-rag-only')
    fake_llm = scripted_call_llm([
        _tool_decision(
            'rag_answer_tool',
            {'question': '请告诉我公司年假制度和报销流程。'},
            'need_knowledge',
        ),
        _finish_decision('年假 5 天；报销 4 步。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.executed_tool_sequence == ['rag_answer_tool']
    assert run.passed is True, f'R10 combined 应 PASS，failures={run.failure_reasons}'
    assert run.required_task_coverage is True
    assert run.sequence_match is True  # [rag_answer_tool] 是合法序列之一


def test_r10_combined_rag_covers_only_one_topic_fail():
    """一次 RAG 只返回年假（未覆盖报销）→ 仍必须 FAIL（coverage 未满）。"""
    case = case_by_id('R10-multi-domain-rag-only')
    fake_llm = scripted_call_llm([
        _tool_decision(
            'rag_answer_tool',
            {'question': '公司年假制度'},
            'need_knowledge',
        ),
        _finish_decision('年假 5 天。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.passed is False
    assert run.required_task_coverage is False
    assert any('required_call_specs_missing' in r for r in run.failure_reasons)


def test_r10_two_separate_rags_pass():
    """拆两次 RAG（年假 → 报销）→ PASS（原合法路径不回归）。"""
    case = case_by_id('R10-multi-domain-rag-only')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _tool_decision('rag_answer_tool',
                       {'question': '公司报销流程'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.executed_tool_sequence == ['rag_answer_tool', 'rag_answer_tool']
    assert run.passed is True, f'R10 两次 RAG 应 PASS，failures={run.failure_reasons}'
    assert run.required_task_coverage is True
    assert run.sequence_match is True


def test_r11_three_step_still_passes():
    """R11 三步（RAG 年假 → Eval → RAG 报销）保持 PASS，不回归。"""
    case = case_by_id('R11-rag-eval-rag')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度'}, 'need_knowledge'),
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _tool_decision('rag_answer_tool',
                       {'question': '公司报销流程'}, 'need_knowledge'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.passed is True, f'R11 应 PASS，failures={run.failure_reasons}'
    assert run.required_task_coverage is True


def test_r11_eval_can_be_first_after_combined():
    """R11 中 eval 之前先用一次合并 RAG 覆盖两 topic 仍须 FAIL：
    因为 R11 的 spec 是 rag(annual_leave) + eval(all) + rag(expense)，
    eval 出现顺序固定（accepted sequence 精确），合并 RAG 只省一次 RAG。"""
    case = case_by_id('R11-rag-eval-rag')
    fake_llm = scripted_call_llm([
        _tool_decision('rag_answer_tool',
                       {'question': '公司年假制度和报销流程'},
                       'need_knowledge'),
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _finish_decision('完成。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    # 覆盖：RAG(annual_leave) RAG(expense) Eval(all) 全齐，coverage OK
    assert run.required_task_coverage is True
    # 但 accepted sequence 必须精确 RAG→Eval→RAG，这里 RAG→Eval 不符合 → FAIL
    assert run.passed is False
    assert run.sequence_match is False


# ── 修复 #4/#5: run 级指标与 trajectory ─────────────────────


def test_run_sequence_match_and_coverage_metrics():
    """run_sequence_match_rate / required_task_coverage_rate 正确聚合。"""
    ok = RealEvalRunResult(
        case_id='a', category='x', run_index=1, trace_id='t1',
        stop_reason='task_complete', route='rag', latency_ms=10,
        passed=True, sequence_match=True, required_task_coverage=True,
        finish_when_complete=True, executed_tool_sequence=['rag_answer_tool'],
    )
    bad = RealEvalRunResult(
        case_id='a', category='x', run_index=2, trace_id='t2',
        stop_reason='task_complete', route='rag', latency_ms=10,
        passed=True, sequence_match=False, required_task_coverage=False,
        finish_when_complete=True, executed_tool_sequence=['rag_answer_tool'],
    )
    cr = RealEvalCaseReport(case_id='a', category='x', question='q',
                            runs=[ok, bad], passed=False, stable=False,
                            pass_count=1)
    metrics = compute_metrics([cr], runs_per_case=2)
    assert metrics['run_sequence_match_rate'] == 0.5
    assert metrics['required_task_coverage_rate'] == 0.5


def test_trajectory_consistency_detects_merged_vs_split():
    """同一 Case：run1 用合并 RAG，run2 拆两次 RAG → 轨迹不同 → 不一致。"""
    merged = RealEvalRunResult(
        case_id='r10', category='x', run_index=1, trace_id='t1',
        stop_reason='task_complete', route='rag', latency_ms=10,
        passed=True, sequence_match=True, required_task_coverage=True,
        finish_when_complete=True,
        executed_tool_sequence=['rag_answer_tool'],
    )
    split = RealEvalRunResult(
        case_id='r10', category='x', run_index=2, trace_id='t2',
        stop_reason='task_complete', route='rag', latency_ms=10,
        passed=True, sequence_match=True, required_task_coverage=True,
        finish_when_complete=True,
        executed_tool_sequence=['rag_answer_tool', 'rag_answer_tool'],
    )
    cr = RealEvalCaseReport(case_id='r10', category='x', question='q',
                            runs=[merged, split], passed=True, stable=True,
                            pass_count=2,
                            trajectory_consistent=False)
    metrics = compute_metrics([cr], runs_per_case=2)
    # 轨迹不同：trajectory_consistency_rate = 0；任务稳定不受影响
    assert cr.trajectory_consistent is False
    assert metrics['trajectory_consistency_rate'] == 0.0
    assert metrics['stable_case_rate'] == 1.0


def test_trajectory_consistency_same_trajectory():
    """所有 Run 轨迹相同 → trajectory_consistent=True。"""
    runs = [
        RealEvalRunResult(
            case_id='a', category='x', run_index=i, trace_id=f't{i}',
            stop_reason='task_complete', route='rag', latency_ms=10,
            passed=True, sequence_match=True, required_task_coverage=True,
            finish_when_complete=True,
            executed_tool_sequence=['rag_answer_tool', 'eval_report_tool'],
        )
        for i in range(1, 4)
    ]
    cr = RealEvalCaseReport(case_id='a', category='x', question='q',
                            runs=runs, passed=True, stable=True, pass_count=3)
    metrics = compute_metrics([cr], runs_per_case=3)
    assert cr.trajectory_consistent is True
    assert metrics['trajectory_consistency_rate'] == 1.0


# ── 修复 #2 补充: eval report_type=all 覆盖 retrieval/generation spec ──


def test_eval_all_covers_generation_spec():
    """R06: Planner 问 generation pass_rate 但用 report_type=all 查询 →
    all 的 Observation 同时覆盖 retrieval + generation + all 三个 topic。
    """
    case = case_by_id('R06-single-eval-generation')
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _finish_decision('生成评估 pass_rate 92%。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.executed_tool_sequence == ['eval_report_tool']
    assert run.required_task_coverage is True
    assert run.passed is True, f'R06 all 查询应 PASS，failures={run.failure_reasons}'


def test_eval_all_covers_retrieval_spec():
    """R07: 问检索命中率用 report_type=all 查询 → 覆盖 retrieval spec。"""
    case = case_by_id('R07-single-eval-retrieval')
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _finish_decision('检索命中率 86%。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.required_task_coverage is True
    assert run.passed is True, f'R07 all 查询应 PASS，failures={run.failure_reasons}'


def test_eval_all_covers_all_spec():
    """R05: spec=(eval, all)，report_type=all 查询 → 覆盖。"""
    case = case_by_id('R05-single-eval-all')
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool',
                       {'report_type': 'all'}, 'need_eval'),
        _finish_decision('检索 88%，生成 92%。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.required_task_coverage is True
    assert run.passed is True, f'R05 应 PASS，failures={run.failure_reasons}'


def test_eval_generation_only_does_not_cover_all_spec():
    """R05: 只查 report_type=generation → 不覆盖 (eval, all) spec → FAIL。"""
    case = case_by_id('R05-single-eval-all')
    fake_llm = scripted_call_llm([
        _tool_decision('eval_report_tool',
                       {'report_type': 'generation'}, 'need_eval'),
        _finish_decision('生成 92%。'),
    ])
    run = run_single_run(case, run_index=1, real_call_llm=fake_llm)
    assert run.required_task_coverage is False
    assert run.passed is False
