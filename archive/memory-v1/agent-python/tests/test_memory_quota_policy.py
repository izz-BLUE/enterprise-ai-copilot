"""Scoped Conversation Memory Phase 5E Quota Policy 测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.memory_quota_policy import MemoryQuotaPolicy


def test_active_plus_upsert_is_allowed():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write('ACTIVE', 'UPSERT') is True


def test_completed_plus_complete_is_allowed_as_idempotent():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write('COMPLETED', 'COMPLETE') is True


def test_abandoned_plus_abandon_is_allowed_as_idempotent():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write('ABANDONED', 'ABANDON') is True


def test_none_existing_allows_initial_upsert():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write(None, 'UPSERT') is True


def test_none_action_is_always_rejected():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write(None, 'NONE') is False
    assert policy.allow_write('ACTIVE', 'NONE') is False
    assert policy.allow_write('COMPLETED', 'NONE') is False
    assert policy.allow_write('ABANDONED', 'NONE') is False


def test_upsert_against_terminal_status_is_rejected():
    policy = MemoryQuotaPolicy()

    # 终态不可改：UPSERT 到 COMPLETED/ABANDONED 不允许。
    assert policy.allow_write('COMPLETED', 'UPSERT') is False
    assert policy.allow_write('ABANDONED', 'UPSERT') is False


def test_terminal_action_against_active_is_rejected():
    policy = MemoryQuotaPolicy()

    # 终态动作只能落到对应终态；ACTIVE 还在进行中时不能直接 COMPLETE/ABANDON
    # （正常完成 / 放弃必须经由 Pipeline 走 UPSERT 之后再转移，P0 这里只兜底）。
    assert policy.allow_write('ACTIVE', 'COMPLETE') is False
    assert policy.allow_write('ACTIVE', 'ABANDON') is False


def test_cross_terminal_actions_are_rejected():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write('COMPLETED', 'ABANDON') is False
    assert policy.allow_write('ABANDONED', 'COMPLETE') is False


def test_unknown_existing_status_is_rejected():
    policy = MemoryQuotaPolicy()

    assert policy.allow_write('UNKNOWN', 'UPSERT') is False
    assert policy.allow_write('paused', 'UPSERT') is False


def test_policy_schema_forbids_extra_fields():
    with pytest.raises(ValidationError):
        MemoryQuotaPolicy(rogue='nope')  # type: ignore[call-arg]


def test_quota_is_deterministic_and_pure():
    policy = MemoryQuotaPolicy()
    cases = [
        (None, 'UPSERT', True),
        ('ACTIVE', 'UPSERT', True),
        ('COMPLETED', 'COMPLETE', True),
        ('ABANDONED', 'ABANDON', True),
        ('COMPLETED', 'UPSERT', False),
        ('ABANDONED', 'UPSERT', False),
        ('ACTIVE', 'COMPLETE', False),
        (None, 'NONE', False),
        ('ACTIVE', 'NONE', False),
        ('UNKNOWN', 'UPSERT', False),
    ]

    for status, action, expected in cases:
        first = policy.allow_write(status, action)
        second = policy.allow_write(status, action)
        assert first == second == expected
