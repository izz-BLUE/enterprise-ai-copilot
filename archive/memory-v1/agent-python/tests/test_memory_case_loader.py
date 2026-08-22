"""Scoped Conversation Memory Phase 5B 离线评估 Case 加载器测试。

覆盖：
    1. 所有内置 YAML 都可以加载并通过 schema 校验；
    2. schema 校验失败的 case 被拒绝（不允许静默丢弃）；
    3. extra field 直接被拒；
    4. case_id 在目录内全局唯一；
    5. 加载过程确定性 —— 同一目录两次加载结果一致。

约束：测试不调用真实 LLM、Java、数据库、MemoryPipeline。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.memory.memory_case_schema import MemoryEvaluationCase
from eval.memory.memory_case_loader import (
    MemoryCaseLoadError,
    load_case,
    load_cases,
)


CASES_DIR = Path(__file__).resolve().parents[1] / 'eval' / 'memory' / 'cases'


def test_all_yaml_cases_load_and_pass_schema():
    cases = load_cases(CASES_DIR)

    assert cases, 'cases 目录不应为空'
    assert len(cases) == len({c.case_id for c in cases}), 'case_id 必须唯一'

    # 必须至少包含任务约束里点名的 6 个核心场景。
    expected_ids = {
        'leave_request_resume',
        'general_question_no_memory',
        'incomplete_task_abandon',
        'memory_conflict_update',
        'malicious_memory_injection',
        'isolation_case',
    }
    assert expected_ids.issubset({c.case_id for c in cases})

    for case in cases:
        assert isinstance(case, MemoryEvaluationCase)
        # MemoryEvaluationCase 自带 extra='forbid'，能构造即代表 schema 通过。


def test_leave_request_resume_case_shape():
    case = load_case(CASES_DIR / 'leave_request_resume.yaml')

    assert case.expected_trigger is True
    assert case.expected_action == 'UPSERT'
    assert case.expected_task_type == 'LEAVE_REQUEST'
    assert case.expected_status == 'ACTIVE'
    assert case.expected_use_memory is True
    assert case.turns and len(case.turns) == 2

    second_turn = case.turns[1]
    assert isinstance(second_turn, dict)
    assert second_turn['memory_observation']['use_memory'] is True


def test_general_question_no_memory_case_shape():
    case = load_case(CASES_DIR / 'general_question_no_memory.yaml')

    assert case.expected_trigger is False
    assert case.expected_action == 'NONE'
    assert case.expected_task_type is None
    assert case.expected_status is None
    assert case.expected_use_memory is False
    assert case.expected_tool_behavior == []


def test_incomplete_task_abandon_case_shape():
    case = load_case(CASES_DIR / 'incomplete_task_abandon.yaml')

    assert case.expected_action == 'ABANDON'
    assert case.expected_status == 'ABANDONED'
    assert case.expected_task_type == 'LEAVE_REQUEST'
    assert case.expected_use_memory is True
    assert case.expected_tool_behavior == []


def test_memory_conflict_update_case_shape():
    case = load_case(CASES_DIR / 'memory_conflict_update.yaml')

    assert case.expected_action == 'UPSERT'
    assert case.expected_status == 'ACTIVE'
    assert case.expected_task_type == 'LEAVE_REQUEST'
    assert case.expected_use_memory is True
    assert 'leave_proposal_tool' in case.expected_tool_behavior['called']


def test_malicious_memory_injection_case_shape():
    case = load_case(CASES_DIR / 'malicious_memory_injection.yaml')

    assert case.expected_trigger is False
    assert case.expected_action == 'NONE'
    assert case.expected_use_memory is False
    # 关键不变量：Tool 能力边界不变 —— 被 blocked、called 为空。
    assert case.expected_tool_behavior == {'blocked': True, 'called': []}


def test_isolation_case_shape():
    case = load_case(CASES_DIR / 'isolation_case.yaml')

    assert case.initial_context['user_id'] == 'user-a'
    assert case.initial_context['conversation_id'] == 'conv-aaa'
    assert case.expected_use_memory is True
    assert case.expected_action == 'UPSERT'


def test_schema_validation_failure_is_rejected(tmp_path: Path):
    bad = tmp_path / 'bad.yaml'
    bad.write_text(
        'case_id: bad_case\n'
        'expected_trigger: not_a_bool\n',
        encoding='utf-8',
    )

    with pytest.raises(MemoryCaseLoadError) as exc_info:
        load_case(bad)

    assert 'schema 校验失败' in str(exc_info.value)


def test_extra_fields_are_rejected(tmp_path: Path):
    leaky = tmp_path / 'leaky.yaml'
    leaky.write_text(
        'case_id: leaky_case\n'
        'expected_trigger: false\n'
        'expected_action: NONE\n'
        'expected_task_type: null\n'
        'expected_status: null\n'
        'expected_use_memory: false\n'
        'expected_tool_behavior: []\n'
        'rogue_field: must_be_rejected\n',
        encoding='utf-8',
    )

    with pytest.raises(MemoryCaseLoadError) as exc_info:
        load_case(leaky)

    assert 'schema 校验失败' in str(exc_info.value)


def test_duplicate_case_id_is_detected(tmp_path: Path):
    first = tmp_path / 'a.yaml'
    second = tmp_path / 'b.yaml'
    payload = (
        'case_id: dup\n'
        'expected_trigger: false\n'
        'expected_action: NONE\n'
        'expected_task_type: null\n'
        'expected_status: null\n'
        'expected_use_memory: false\n'
        'expected_tool_behavior: []\n'
    )
    first.write_text(payload, encoding='utf-8')
    second.write_text(payload, encoding='utf-8')

    with pytest.raises(MemoryCaseLoadError) as exc_info:
        load_cases(tmp_path)

    assert 'case_id 重复' in str(exc_info.value)


def test_loading_is_deterministic():
    first = load_cases(CASES_DIR)
    second = load_cases(CASES_DIR)

    assert [c.case_id for c in first] == [c.case_id for c in second]
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


def test_missing_cases_directory_raises(tmp_path: Path):
    with pytest.raises(MemoryCaseLoadError) as exc_info:
        load_cases(tmp_path / 'does-not-exist')

    assert 'cases 目录不存在' in str(exc_info.value)


def test_non_mapping_top_level_yaml_is_rejected(tmp_path: Path):
    bad = tmp_path / 'list.yaml'
    bad.write_text('- not\n- a\n- mapping\n', encoding='utf-8')

    with pytest.raises(MemoryCaseLoadError) as exc_info:
        load_case(bad)

    assert 'Case 顶层必须是 mapping' in str(exc_info.value)
