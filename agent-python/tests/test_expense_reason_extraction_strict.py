"""Regression coverage for strict reimbursement-reason extraction."""

import pytest

from app.services.expense_input_service import extract_expense_reason


def test_reason_question_is_not_treated_as_explicit_assignment():
    assert extract_expense_reason("报销说明应该填什么？") is None


def test_reason_assignment_with_colon_remains_supported():
    assert extract_expense_reason("报销说明：项目A售前支持") == "项目A售前支持"


def test_reason_assignment_with_write_verb_remains_supported():
    assert extract_expense_reason("报销原因写“项目A售前客户拜访”") == "项目A售前客户拜访"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("报销原因为：去拜访客户", "去拜访客户"),
        ("报销原因为: 去拜访客户", "去拜访客户"),
        ("报销原因为 = 去拜访客户", "去拜访客户"),
        ("报销原因＝去拜访客户", "去拜访客户"),
        ("报销原因写：去拜访客户", "去拜访客户"),
        ("报销原因填写为：去拜访客户", "去拜访客户"),
        ("报销原因填成为＝去拜访客户", "去拜访客户"),
        ("报销原因写成为=去拜访客户", "去拜访客户"),
    ],
)
def test_reason_assignment_consumes_supported_separators(question, expected):
    assert extract_expense_reason(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "报销原因是什么？",
        "报销原因为：什么？",
        "报销原因写什么？",
        "报销原因填写为＝什么？",
    ],
)
def test_reason_questions_remain_unmatched_with_assignment_separators(question):
    assert extract_expense_reason(question) is None
