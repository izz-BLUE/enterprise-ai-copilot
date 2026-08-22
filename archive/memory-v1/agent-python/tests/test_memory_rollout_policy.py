"""Scoped Conversation Memory Phase 5E Rollout Policy 测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.memory_rollout_policy import MemoryRolloutPolicy


def test_zero_percentage_never_enables():
    policy = MemoryRolloutPolicy(enabled=True, percentage=0)

    assert policy.should_enable('user-1') is False
    assert policy.should_enable('user-2') is False
    assert policy.should_enable('') is False
    assert policy.should_enable('a-very-long-subject-id') is False


def test_hundred_percentage_always_enables_when_flag_on():
    policy = MemoryRolloutPolicy(enabled=True, percentage=100)

    assert policy.should_enable('user-1') is True
    assert policy.should_enable('user-2') is True
    assert policy.should_enable('any-random-id') is True


def test_disabled_flag_never_enables_even_at_full_rollout():
    policy = MemoryRolloutPolicy(enabled=False, percentage=100)

    assert policy.should_enable('user-1') is False
    assert policy.should_enable('user-2') is False


def test_hash_bucket_is_deterministic_for_same_subject():
    policy = MemoryRolloutPolicy(enabled=True, percentage=50)

    first = policy.should_enable('alice@corp')
    second = policy.should_enable('alice@corp')
    third = policy.should_enable('alice@corp')

    assert first == second == third


def test_hash_bucket_distributes_subjects_consistently():
    policy = MemoryRolloutPolicy(enabled=True, percentage=50)

    # 同一 subject 在不同 policy 实例上应保持同样的决定（无随机性）。
    other = MemoryRolloutPolicy(enabled=True, percentage=50)
    subjects = [f'user-{i}' for i in range(200)]
    first_pass = [policy.should_enable(s) for s in subjects]
    second_pass = [other.should_enable(s) for s in subjects]
    assert first_pass == second_pass

    # 50% 灰度下，200 个 subject 应有非零且合理的命中数。
    hits = sum(first_pass)
    assert 50 < hits < 150


def test_invalid_percentage_is_rejected():
    with pytest.raises(ValidationError):
        MemoryRolloutPolicy(enabled=True, percentage=-1)

    with pytest.raises(ValidationError):
        MemoryRolloutPolicy(enabled=True, percentage=101)


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        MemoryRolloutPolicy(enabled=True, percentage=10, rogue='x')  # type: ignore[call-arg]


def test_empty_or_invalid_subject_id_defaults_to_disabled():
    policy = MemoryRolloutPolicy(enabled=True, percentage=100)

    assert policy.should_enable('') is False
    # 非字符串安全默认 False。
    assert policy.should_enable(None) is False  # type: ignore[arg-type]
