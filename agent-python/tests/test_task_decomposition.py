"""Tests for the bounded, deterministic write-task decomposition contract."""

import pytest
from pydantic import ValidationError

from app.schemas.task_decomposition_schema import TaskSpec
from app.services.task_decomposition_service import decompose_write_tasks


def test_leave_then_expense_preserves_original_order_and_spans():
    question = "帮我下周一到周二请年假，原因个人安排，另外把最近一次已批准的出差和对应发票报销掉。"

    result = decompose_write_tasks(question)

    assert result.kind == "multi"
    assert [task.task_type for task in result.tasks] == [
        "LEAVE_REQUEST",
        "EXPENSE_CLAIM",
    ]
    assert [task.sequence for task in result.tasks] == [1, 2]
    assert result.tasks[0].task_text == "帮我下周一到周二请年假，原因个人安排"
    assert result.tasks[1].task_text == "把最近一次已批准的出差和对应发票报销掉。"
    assert all(task.task_text in question for task in result.tasks)


def test_expense_then_leave_preserves_reverse_order():
    question = "请先把最近一次已批准的出差和对应发票报销掉，然后帮我下周一到周二请年假。"

    result = decompose_write_tasks(question)

    assert result.kind == "multi"
    assert [task.task_type for task in result.tasks] == [
        "EXPENSE_CLAIM",
        "LEAVE_REQUEST",
    ]
    assert result.tasks[0].task_text == "请先把最近一次已批准的出差和对应发票报销掉"
    assert result.tasks[1].task_text == "帮我下周一到周二请年假。"


def test_generic_leave_phrase_is_decomposed_for_clarification_flow():
    result = decompose_write_tasks("帮我请个假，然后把最近一次已批准的出差报销。")

    assert result.kind == "multi"
    assert result.tasks[0].task_type == "LEAVE_REQUEST"
    assert result.tasks[0].task_text == "帮我请个假"
    assert result.tasks[1].task_type == "EXPENSE_CLAIM"


def test_single_write_task_keeps_legacy_path_without_decomposition_tasks():
    assert decompose_write_tasks("申请2026-09-01一天年假，原因为私事").kind == "single"
    assert decompose_write_tasks("把最近一次已批准的出差报销掉").kind == "single"
    assert decompose_write_tasks("公司的年假制度和报销流程是什么").kind == "single"


def test_unsupported_third_write_task_is_fail_closed():
    result = decompose_write_tasks("先申请年假，然后把出差报销，最后提交加班申请。")

    assert result.kind == "unsupported"
    assert result.tasks == []


def test_three_supported_write_tasks_are_fail_closed():
    result = decompose_write_tasks("先申请年假，然后把出差报销，最后再申请年假。")

    assert result.kind == "unsupported"
    assert result.tasks == []


def test_two_write_intents_without_safe_boundary_are_fail_closed():
    result = decompose_write_tasks("请年假报销最近一次出差")

    assert result.kind == "unsupported"
    assert result.tasks == []


def test_task_spec_is_pure_and_rejects_lifecycle_fields():
    with pytest.raises(ValidationError):
        TaskSpec(
            task_type="LEAVE_REQUEST",
            task_text="请年假",
            sequence=1,
            status="RUNNING",
        )
