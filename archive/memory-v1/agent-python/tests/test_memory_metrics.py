"""Scoped Conversation Memory Phase 5C MemoryMetricsCollector 测试。

约束：测试不连接 Prometheus / 数据库 / Kafka / RuntimeHook；
仅在内存中对 ``MemoryAuditEvent`` 做聚合。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.memory_audit import MemoryAuditEvent
from app.memory.memory_metrics import (
    MemoryMetricsCollector,
    MemoryMetricsSnapshot,
    aggregate_events,
)


def _event(**overrides) -> MemoryAuditEvent:
    payload = {
        'triggered': False,
        'trigger_reason': '',
        'proposal_action': None,
        'task_type': None,
        'write_attempted': False,
        'write_success': False,
        'error_type': None,
    }
    payload.update(overrides)
    return MemoryAuditEvent(**payload)


def test_empty_collector_returns_zero_snapshot():
    snapshot = MemoryMetricsCollector().snapshot()

    assert snapshot == MemoryMetricsSnapshot()
    assert snapshot.trigger_total == 0
    assert snapshot.write_attempt_total == 0
    assert snapshot.write_success_total == 0
    assert snapshot.write_failure_total == 0
    assert snapshot.trigger_reason_counts == {}
    assert snapshot.action_counts == {}
    assert snapshot.error_counts == {}
    assert snapshot.success_ratio is None


def test_single_event_aggregates_expected_fields():
    collector = MemoryMetricsCollector()
    collector.record_event(
        _event(
            triggered=True,
            trigger_reason='leave_request_in_progress',
            proposal_action='UPSERT',
            task_type='LEAVE_REQUEST',
            write_attempted=True,
            write_success=True,
        ),
    )

    snap = collector.snapshot()

    assert snap.trigger_total == 1
    assert snap.write_attempt_total == 1
    assert snap.write_success_total == 1
    assert snap.write_failure_total == 0
    assert snap.trigger_reason_counts == {'leave_request_in_progress': 1}
    assert snap.action_counts == {'UPSERT': 1}
    assert snap.error_counts == {}
    assert snap.success_ratio == 1.0


def test_multiple_events_aggregate_counts():
    collector = MemoryMetricsCollector()
    events = [
        _event(triggered=True, trigger_reason='leave_in_progress',
               proposal_action='UPSERT', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=True),
        _event(triggered=True, trigger_reason='leave_in_progress',
               proposal_action='ABANDON', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=True),
        _event(triggered=True, trigger_reason='abandon_signal',
               proposal_action='ABANDON', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=False,
               error_type='DispatcherError'),
        _event(triggered=False, trigger_reason='',
               proposal_action=None, task_type=None,
               write_attempted=False, write_success=False,
               error_type='PipelineError'),
    ]
    for event in events:
        collector.record_event(event)

    snap = collector.snapshot()

    assert snap.trigger_total == 3
    assert snap.write_attempt_total == 3
    assert snap.write_success_total == 2
    assert snap.write_failure_total == 1
    assert snap.trigger_reason_counts == {
        'abandon_signal': 1,
        'leave_in_progress': 2,
    }
    assert snap.action_counts == {'ABANDON': 2, 'UPSERT': 1}
    assert snap.error_counts == {'DispatcherError': 1, 'PipelineError': 1}


def test_success_failure_ratio_matches_attempts():
    collector = MemoryMetricsCollector()
    for success in (True, True, False, False, True):
        collector.record_event(
            _event(
                triggered=True,
                trigger_reason='r',
                proposal_action='UPSERT',
                task_type='GENERIC',
                write_attempted=True,
                write_success=success,
                error_type=None if success else 'DispatcherError',
            ),
        )

    snap = collector.snapshot()

    assert snap.write_attempt_total == 5
    assert snap.write_success_total == 3
    assert snap.write_failure_total == 2
    assert snap.success_ratio == pytest.approx(0.6)
    assert snap.error_counts == {'DispatcherError': 2}


def test_trigger_reason_aggregation_distinguishes_reasons():
    collector = MemoryMetricsCollector()
    plan = [
        ('reason_a', 'UPSERT'),
        ('reason_a', 'UPSERT'),
        ('reason_b', 'COMPLETE'),
        ('reason_c', 'ABANDON'),
    ]
    for reason, action in plan:
        collector.record_event(
            _event(
                triggered=True,
                trigger_reason=reason,
                proposal_action=action,
                task_type='LEAVE_REQUEST',
                write_attempted=True,
                write_success=True,
            ),
        )

    snap = collector.snapshot()

    assert snap.trigger_total == 4
    assert snap.trigger_reason_counts == {
        'reason_a': 2,
        'reason_b': 1,
        'reason_c': 1,
    }
    assert snap.action_counts == {
        'ABANDON': 1,
        'COMPLETE': 1,
        'UPSERT': 2,
    }


def test_action_aggregation_groups_by_action_label():
    collector = MemoryMetricsCollector()
    for action in ('UPSERT', 'NONE', 'UPSERT', 'COMPLETE', 'ABANDON'):
        collector.record_event(
            _event(
                triggered=action != 'NONE',
                trigger_reason='r',
                proposal_action=action,
                task_type='LEAVE_REQUEST',
                write_attempted=action != 'NONE',
                write_success=action != 'NONE',
            ),
        )

    snap = collector.snapshot()

    assert snap.trigger_total == 4  # NONE 跳过 trigger 计数
    assert snap.action_counts == {
        'ABANDON': 1,
        'COMPLETE': 1,
        'NONE': 1,
        'UPSERT': 2,
    }


def test_error_aggregation_collects_only_error_type_events():
    collector = MemoryMetricsCollector()
    cases = [
        ('DispatcherError', True, False),
        ('DispatcherError', True, False),
        ('PipelineError', False, False),
        ('AuditError', True, False),
        (None, True, True),  # 成功路径不应进入 error_counts
    ]
    for error_type, write_attempted, write_success in cases:
        collector.record_event(
            _event(
                triggered=write_attempted,
                trigger_reason='r',
                proposal_action='UPSERT' if write_attempted else None,
                task_type='LEAVE_REQUEST' if write_attempted else None,
                write_attempted=write_attempted,
                write_success=write_success,
                error_type=error_type,
            ),
        )

    snap = collector.snapshot()

    assert snap.error_counts == {
        'AuditError': 1,
        'DispatcherError': 2,
        'PipelineError': 1,
    }


def test_reset_clears_all_counters():
    collector = MemoryMetricsCollector()
    for _ in range(3):
        collector.record_event(
            _event(
                triggered=True,
                trigger_reason='r',
                proposal_action='UPSERT',
                task_type='LEAVE_REQUEST',
                write_attempted=True,
                write_success=True,
            ),
        )
    assert collector.event_total == 3
    assert collector.snapshot().trigger_total == 3

    collector.reset()

    assert collector.event_total == 0
    assert collector.snapshot() == MemoryMetricsSnapshot()


def test_record_event_rejects_non_audit_event():
    collector = MemoryMetricsCollector()

    with pytest.raises(TypeError):
        collector.record_event({'triggered': True})  # type: ignore[arg-type]


def test_snapshot_is_frozen_and_deterministic():
    collector = MemoryMetricsCollector()
    events = [
        _event(triggered=True, trigger_reason='z_reason',
               proposal_action='UPSERT', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=True),
        _event(triggered=True, trigger_reason='a_reason',
               proposal_action='ABANDON', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=False,
               error_type='DispatcherError'),
        _event(triggered=True, trigger_reason='m_reason',
               proposal_action='COMPLETE', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=True,
               error_type=None),
    ]
    for event in events:
        collector.record_event(event)

    snap1 = collector.snapshot()
    snap2 = collector.snapshot()

    # snapshot 是冻结的，尝试修改应该抛错。
    with pytest.raises(ValidationError):
        snap1.trigger_total = 999  # type: ignore[misc]

    # 两次调用结果一致；counts 按 key 升序。
    assert snap1 == snap2
    assert list(snap1.trigger_reason_counts) == sorted(snap1.trigger_reason_counts)
    assert list(snap1.action_counts) == sorted(snap1.action_counts)
    assert list(snap1.error_counts) == sorted(snap1.error_counts)


def test_snapshot_forbids_extra_fields():
    with pytest.raises(ValidationError):
        MemoryMetricsSnapshot(rogue_field=1)  # type: ignore[call-arg]


def test_snapshot_rejects_negative_counters():
    with pytest.raises(ValidationError):
        MemoryMetricsSnapshot(trigger_total=-1)

    with pytest.raises(ValidationError):
        MemoryMetricsSnapshot(
            trigger_reason_counts={'r': -3},
        )

    with pytest.raises(ValidationError):
        MemoryMetricsSnapshot(write_success_total=-1)


def test_record_event_does_not_mutate_input():
    event = _event(
        triggered=True,
        trigger_reason='r',
        proposal_action='UPSERT',
        task_type='LEAVE_REQUEST',
        write_attempted=True,
        write_success=True,
    )
    before = event.model_dump()
    collector = MemoryMetricsCollector()
    collector.record_event(event)
    after = event.model_dump()

    assert before == after


def test_aggregate_events_helper_is_one_shot():
    events = [
        _event(triggered=True, trigger_reason='r1',
               proposal_action='UPSERT', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=True),
        _event(triggered=True, trigger_reason='r2',
               proposal_action='ABANDON', task_type='LEAVE_REQUEST',
               write_attempted=True, write_success=False,
               error_type='DispatcherError'),
    ]
    snap = aggregate_events(events)

    assert snap.trigger_total == 2
    assert snap.write_attempt_total == 2
    assert snap.write_success_total == 1
    assert snap.write_failure_total == 1
    assert snap.error_counts == {'DispatcherError': 1}
