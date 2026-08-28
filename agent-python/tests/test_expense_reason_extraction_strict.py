"""Regression coverage for strict reimbursement-reason extraction."""

from app.services.expense_input_service import extract_expense_reason


def test_reason_question_is_not_treated_as_explicit_assignment():
    assert extract_expense_reason("报销说明应该填什么？") is None


def test_reason_assignment_with_colon_remains_supported():
    assert extract_expense_reason("报销说明：项目A售前支持") == "项目A售前支持"


def test_reason_assignment_with_write_verb_remains_supported():
    assert extract_expense_reason("报销原因写“项目A售前客户拜访”") == "项目A售前客户拜访"
