"""Scoped Conversation Memory Phase 5F Release Gate 测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.memory_metrics import MemoryMetricsSnapshot
from eval.memory.memory_release_audit import (
    MemoryCostSummary,
    MemoryEvaluationSummary,
    MemoryGuardRailStatus,
    MemoryReleaseAuditResult,
)
from eval.memory.memory_release_evaluator import (
    MemoryReleaseEvaluator,
    ReleaseThresholds,
)


# --- 工厂 ----------------------------------------------------------------------


def _metrics(**overrides) -> MemoryMetricsSnapshot:
    payload = {
        'trigger_total': 10,
        'write_attempt_total': 8,
        'write_success_total': 8,
        'write_failure_total': 0,
        'trigger_reason_counts': {},
        'action_counts': {'UPSERT': 6, 'COMPLETE': 2},
        'error_counts': {},
    }
    payload.update(overrides)
    return MemoryMetricsSnapshot(**payload)


def _evaluation(**overrides) -> MemoryEvaluationSummary:
    payload = {
        'case_total': 6,
        'passed_total': 6,
        'harm_total': 0,
        'mean_score': 1.0,
    }
    payload.update(overrides)
    return MemoryEvaluationSummary(**payload)


def _cost(**overrides) -> MemoryCostSummary:
    payload = {
        'sample_total': 6,
        'positive_roi_total': 5,
        'mean_latency_overhead': 100.0,
    }
    payload.update(overrides)
    return MemoryCostSummary(**payload)


def _guard_rail(**overrides) -> MemoryGuardRailStatus:
    payload = {
        'rollout_enabled': True,
        'rollout_percentage': 10,
        'quota_ok': True,
        'safety_ok': True,
    }
    payload.update(overrides)
    return MemoryGuardRailStatus(**payload)


# --- happy path ----------------------------------------------------------------


def test_all_pass_yields_ready():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.safety_pass is True
    assert result.rollout_pass is True
    assert result.isolation_pass is True
    assert result.evaluation_pass is True
    assert result.cost_pass is True
    assert result.enabled_recommendation == 'READY'
    assert result.blockers == []


# --- safety --------------------------------------------------------------------


def test_safety_fail_when_guard_rail_safety_disabled():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(safety_ok=False),
    )

    assert result.safety_pass is False
    assert result.enabled_recommendation == 'BLOCKED'
    assert any(b.startswith('safety:') for b in result.blockers)


def test_safety_fail_when_write_failure_present():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(write_failure_total=2),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.safety_pass is False
    assert 'write_failure_total=2' in ' '.join(result.blockers)


def test_safety_fail_when_error_counts_non_zero():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(error_counts={'DispatcherError': 1}),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.safety_pass is False
    assert any('error_counts' in b for b in result.blockers)


# --- isolation -----------------------------------------------------------------


def test_isolation_fail_when_harm_detected():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(harm_total=1),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.isolation_pass is False
    assert result.enabled_recommendation == 'BLOCKED'
    assert any(b.startswith('isolation:') for b in result.blockers)


# --- evaluation / cost ---------------------------------------------------------


def test_evaluation_fail_when_no_cases():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(case_total=0, passed_total=0, mean_score=0.0),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.evaluation_pass is False
    assert any('case_total == 0' in b for b in result.blockers)


def test_evaluation_fail_when_mean_score_below_threshold():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(mean_score=0.5),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    assert result.evaluation_pass is False
    assert any('mean_score' in b for b in result.blockers)


def test_cost_fail_when_no_samples():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(sample_total=0, positive_roi_total=0, mean_latency_overhead=0.0),
        guard_rail=_guard_rail(),
    )

    assert result.cost_pass is False
    assert any('sample_total == 0' in b for b in result.blockers)


def test_cost_fail_when_latency_overhead_exceeds_threshold():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(mean_latency_overhead=2000.0),
        guard_rail=_guard_rail(),
    )

    assert result.cost_pass is False
    assert any('mean_latency_overhead' in b for b in result.blockers)


def test_cost_fail_when_roi_negative_for_all_samples():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(positive_roi_total=0),
        guard_rail=_guard_rail(),
    )

    assert result.cost_pass is False
    assert any('positive_roi_total' in b for b in result.blockers)


# --- rollout -------------------------------------------------------------------


def test_rollout_fail_when_disabled():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(rollout_enabled=False),
    )

    assert result.rollout_pass is False
    assert result.enabled_recommendation == 'BLOCKED'


def test_rollout_fail_when_percentage_zero():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(rollout_percentage=0),
    )

    assert result.rollout_pass is False


def test_rollout_fail_when_quota_not_ok():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(quota_ok=False),
    )

    assert result.rollout_pass is False


# --- 多项失败合并 --------------------------------------------------------------


def test_multiple_blockers_are_combined_in_deterministic_order():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(write_failure_total=1, error_counts={'X': 1}),
        evaluation=_evaluation(case_total=0, passed_total=0, harm_total=2,
                               mean_score=0.1),
        cost=_cost(sample_total=0, positive_roi_total=0,
                   mean_latency_overhead=1000.0),
        guard_rail=_guard_rail(
            rollout_enabled=False, rollout_percentage=0, quota_ok=False,
            safety_ok=False,
        ),
    )

    assert result.enabled_recommendation == 'BLOCKED'
    # 确定性顺序：safety → rollout → isolation → evaluation → cost
    # 同 section 内部可能有多个 blocker（连续出现），但 section 顺序必须固定。
    expected_order = ['safety', 'rollout', 'isolation', 'evaluation', 'cost']
    sections = [b.split(':', 1)[0] for b in result.blockers]
    # 把连续相同的 section 压缩成 section 序列，再与期望比较
    compressed: list[str] = []
    for section in sections:
        if not compressed or compressed[-1] != section:
            compressed.append(section)
    assert compressed == expected_order
    # 同一组输入再次评估，顺序依然一致
    again = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(write_failure_total=1, error_counts={'X': 1}),
        evaluation=_evaluation(case_total=0, passed_total=0, harm_total=2,
                               mean_score=0.1),
        cost=_cost(sample_total=0, positive_roi_total=0,
                   mean_latency_overhead=1000.0),
        guard_rail=_guard_rail(
            rollout_enabled=False, rollout_percentage=0, quota_ok=False,
            safety_ok=False,
        ),
    )
    again_sections = [b.split(':', 1)[0] for b in again.blockers]
    again_compressed: list[str] = []
    for section in again_sections:
        if not again_compressed or again_compressed[-1] != section:
            again_compressed.append(section)
    assert again_compressed == expected_order


def test_blockers_are_deterministic_for_same_input():
    evaluator = MemoryReleaseEvaluator()
    kwargs = dict(
        metrics=_metrics(write_failure_total=1),
        evaluation=_evaluation(harm_total=1, mean_score=0.3),
        cost=_cost(sample_total=0),
        guard_rail=_guard_rail(safety_ok=False),
    )

    first = evaluator.evaluate(**kwargs)
    second = evaluator.evaluate(**kwargs)

    assert first.blockers == second.blockers
    assert first.enabled_recommendation == second.enabled_recommendation == 'BLOCKED'


# --- schema 防御 ---------------------------------------------------------------


def test_result_schema_forbids_extra_fields():
    result = MemoryReleaseEvaluator().evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(),
        cost=_cost(),
        guard_rail=_guard_rail(),
    )

    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(**result.model_dump(), extra=1)  # type: ignore[call-arg]


def test_release_audit_result_does_not_carry_sensitive_fields():
    """``MemoryReleaseAuditResult`` schema 本身禁止敏感字段作为键名。"""
    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(
            safety_pass=True,
            rollout_pass=True,
            isolation_pass=True,
            evaluation_pass=True,
            cost_pass=True,
            enabled_recommendation='READY',
            user_id='leak',  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(
            safety_pass=True,
            rollout_pass=True,
            isolation_pass=True,
            evaluation_pass=True,
            cost_pass=True,
            enabled_recommendation='READY',
            employee_id='leak',  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(
            safety_pass=True,
            rollout_pass=True,
            isolation_pass=True,
            evaluation_pass=True,
            cost_pass=True,
            enabled_recommendation='READY',
            conversation_id='leak',  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(
            safety_pass=True,
            rollout_pass=True,
            isolation_pass=True,
            evaluation_pass=True,
            cost_pass=True,
            enabled_recommendation='READY',
            summary='leak',  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        MemoryReleaseAuditResult(
            safety_pass=True,
            rollout_pass=True,
            isolation_pass=True,
            evaluation_pass=True,
            cost_pass=True,
            enabled_recommendation='READY',
            task_state={'leak': 1},  # type: ignore[call-arg]
        )


def test_input_summaries_forbid_sensitive_fields():
    with pytest.raises(ValidationError):
        MemoryEvaluationSummary(  # type: ignore[call-arg]
            case_total=1,
            passed_total=1,
            harm_total=0,
            mean_score=1.0,
            user_id='leak',
        )

    with pytest.raises(ValidationError):
        MemoryCostSummary(  # type: ignore[call-arg]
            sample_total=1,
            positive_roi_total=1,
            mean_latency_overhead=0.0,
            employee_id='leak',
        )

    with pytest.raises(ValidationError):
        MemoryGuardRailStatus(  # type: ignore[call-arg]
            rollout_enabled=True,
            rollout_percentage=10,
            quota_ok=True,
            safety_ok=True,
            conversation_id='leak',
        )


# --- 阈值覆盖 -----------------------------------------------------------------


def test_thresholds_can_be_overridden():
    custom = ReleaseThresholds(min_mean_score=0.99, max_mean_overhead_ms=50.0)
    evaluator = MemoryReleaseEvaluator(custom)

    result = evaluator.evaluate(
        metrics=_metrics(),
        evaluation=_evaluation(mean_score=0.95),
        cost=_cost(mean_latency_overhead=200.0),
        guard_rail=_guard_rail(),
    )

    assert result.evaluation_pass is False
    assert result.cost_pass is False
    assert result.enabled_recommendation == 'BLOCKED'
