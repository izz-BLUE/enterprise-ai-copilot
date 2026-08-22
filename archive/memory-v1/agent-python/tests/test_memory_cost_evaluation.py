"""Scoped Conversation Memory Phase 5D 离线成本 / 延迟评估器测试。

约束：测试不调用 LLM / Java / 数据库 / MemoryPipeline / Runtime hook。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.memory.memory_cost_evaluator import (
    MemoryCostEvaluator,
    MemoryCostResult,
)
from eval.memory.memory_cost_schema import MemoryCostSample


def _sample(**overrides) -> MemoryCostSample:
    payload = {
        'case_id': 'cost-case',
        'baseline_latency_ms': 1000.0,
        'memory_latency_ms': 1200.0,
        'extractor_input_tokens': 200,
        'extractor_output_tokens': 50,
        'planner_memory_tokens': 100,
        'success_without_memory': True,
        'success_with_memory': True,
    }
    payload.update(overrides)
    return MemoryCostSample(**payload)


def test_latency_overhead_is_difference():
    sample = _sample(baseline_latency_ms=800.0, memory_latency_ms=1250.0)
    result = MemoryCostEvaluator().evaluate(sample)

    assert result.latency_overhead_ms == 450.0
    # 450 / 800 = 0.5625
    assert result.latency_overhead_ratio == pytest.approx(0.5625)


def test_token_total_sums_three_dimensions():
    sample = _sample(
        extractor_input_tokens=120,
        extractor_output_tokens=33,
        planner_memory_tokens=7,
    )
    result = MemoryCostEvaluator().evaluate(sample)

    assert result.token_total == 160


def test_recovery_improvement_truth_table():
    evaluator = MemoryCostEvaluator()

    assert evaluator.evaluate(
        _sample(success_without_memory=True, success_with_memory=True),
    ).recovery_improvement == 0

    assert evaluator.evaluate(
        _sample(success_without_memory=False, success_with_memory=True),
    ).recovery_improvement == 1

    assert evaluator.evaluate(
        _sample(success_without_memory=True, success_with_memory=False),
    ).recovery_improvement == -1

    assert evaluator.evaluate(
        _sample(success_without_memory=False, success_with_memory=False),
    ).recovery_improvement == 0


def test_recovery_improvement_is_none_when_either_flag_missing():
    evaluator = MemoryCostEvaluator()

    assert evaluator.evaluate(
        _sample(success_without_memory=None, success_with_memory=True),
    ).recovery_improvement is None

    assert evaluator.evaluate(
        _sample(success_without_memory=True, success_with_memory=None),
    ).recovery_improvement is None

    assert evaluator.evaluate(
        _sample(success_without_memory=None, success_with_memory=None),
    ).recovery_improvement is None


def test_roi_score_uses_deterministic_formula():
    evaluator = MemoryCostEvaluator()

    # recovery = 1, overhead = 500ms, token_total = 1000
    # 1 - 0.5 - 0.1 = 0.4
    sample = _sample(
        baseline_latency_ms=1000.0,
        memory_latency_ms=1500.0,
        extractor_input_tokens=400,
        extractor_output_tokens=200,
        planner_memory_tokens=400,
        success_without_memory=False,
        success_with_memory=True,
    )
    assert evaluator.evaluate(sample).roi_score == pytest.approx(0.4)

    # recovery = -1, overhead = 2000ms, token_total = 5000
    # -1 - 2.0 - 0.5 = -3.5
    sample = _sample(
        baseline_latency_ms=1000.0,
        memory_latency_ms=3000.0,
        extractor_input_tokens=2000,
        extractor_output_tokens=1000,
        planner_memory_tokens=2000,
        success_without_memory=True,
        success_with_memory=False,
    )
    assert evaluator.evaluate(sample).roi_score == pytest.approx(-3.5)


def test_roi_score_is_none_when_recovery_missing():
    evaluator = MemoryCostEvaluator()

    sample = _sample(success_without_memory=None, success_with_memory=True)
    result = evaluator.evaluate(sample)

    assert result.recovery_improvement is None
    assert result.roi_score is None


def test_zero_latency_baseline_yields_none_ratio():
    sample = _sample(baseline_latency_ms=0.0, memory_latency_ms=120.0)
    result = MemoryCostEvaluator().evaluate(sample)

    assert result.latency_overhead_ms == 120.0
    assert result.latency_overhead_ratio is None


def test_zero_overhead_yields_zero_ratio_and_balanced_roi():
    sample = _sample(
        baseline_latency_ms=1000.0,
        memory_latency_ms=1000.0,
        extractor_input_tokens=0,
        extractor_output_tokens=0,
        planner_memory_tokens=0,
        success_without_memory=True,
        success_with_memory=True,
    )
    result = MemoryCostEvaluator().evaluate(sample)

    assert result.latency_overhead_ms == 0.0
    assert result.latency_overhead_ratio == 0.0
    assert result.token_total == 0
    assert result.recovery_improvement == 0
    assert result.roi_score == 0.0


def test_negative_values_are_rejected_by_schema():
    with pytest.raises(ValidationError):
        _sample(baseline_latency_ms=-1.0)

    with pytest.raises(ValidationError):
        _sample(extractor_input_tokens=-5)

    with pytest.raises(ValidationError):
        _sample(planner_memory_tokens=-1)


def test_extra_fields_are_rejected_by_schema():
    with pytest.raises(ValidationError):
        MemoryCostSample(  # type: ignore[call-arg]
            case_id='x',
            baseline_latency_ms=1.0,
            memory_latency_ms=1.0,
            extractor_input_tokens=0,
            extractor_output_tokens=0,
            planner_memory_tokens=0,
            rogue='nope',
        )


def test_result_schema_forbids_extra_fields():
    sample = _sample()
    result = MemoryCostEvaluator().evaluate(sample)

    with pytest.raises(ValidationError):
        MemoryCostResult(**result.model_dump(), extra_field=1)  # type: ignore[call-arg]


def test_evaluation_is_deterministic_and_does_not_mutate_sample():
    sample = _sample(
        baseline_latency_ms=900.0,
        memory_latency_ms=1300.0,
        extractor_input_tokens=120,
        extractor_output_tokens=40,
        planner_memory_tokens=80,
        success_without_memory=False,
        success_with_memory=True,
    )
    before = sample.model_dump()
    evaluator = MemoryCostEvaluator()

    first = evaluator.evaluate(sample)
    second = evaluator.evaluate(sample)

    assert first == second
    assert sample.model_dump() == before

    expected = MemoryCostResult(
        case_id=sample.case_id,
        latency_overhead_ms=400.0,
        latency_overhead_ratio=round(400.0 / 900.0, 6),
        token_total=240,
        recovery_improvement=1,
        # ROI: 1 - 0.4 - (240/10000) = 1 - 0.4 - 0.024 = 0.576
        roi_score=round(1 - 0.4 - (240 / 10000.0), 6),
    )
    assert first == expected
    # 同时确认关键字段的浮点容差（防止 6 位 round 之外的尾数漂移）。
    assert first.latency_overhead_ratio == pytest.approx(400.0 / 900.0)
    assert first.roi_score == pytest.approx(0.576)


def test_evaluator_rejects_non_sample_input():
    evaluator = MemoryCostEvaluator()

    with pytest.raises(TypeError):
        evaluator.evaluate({'case_id': 'x'})  # type: ignore[arg-type]
