from datetime import date

import pytest

from app.services.annual_leave_input_service import (
    AnnualLeaveInputError,
    analyze_annual_leave_input,
    is_annual_leave_action_intent,
)


BUSINESS_DATE = date(2026, 7, 16)


@pytest.mark.parametrize(
    "question",
    [
        "申请2026-07-20一天年假，原因为私事",
        "我想请明天一天年假，因为需要就医",
        "我要申请7月20日至7月22日年假，原因为家庭事务",
        "申请2026-07-20下午半天年假，原因为就医",
        "申请一天年假，原因为私事",
        "申请2026-07-20一天年假",
    ],
)
def test_conservative_action_intent_accepts_explicit_requests(question):
    assert is_annual_leave_action_intent(question)


@pytest.mark.parametrize(
    "question",
    [
        "公司的年假政策是什么",
        "年假一年有多少天",
        "年假余额怎么计算",
        "年假可以结转吗",
        "什么是年假",
        "年假审批流程是什么",
    ],
)
def test_policy_and_balance_questions_remain_rag(question):
    assert not is_annual_leave_action_intent(question)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("申请2026-07-20一天年假，原因为私事", date(2026, 7, 20)),
        ("申请2026年7月20日一天年假，原因为私事", date(2026, 7, 20)),
        ("申请7月20日一天年假，原因为私事", date(2026, 7, 20)),
        ("申请明天一天年假，原因为私事", date(2026, 7, 17)),
        ("申请后天一天年假，原因为私事", date(2026, 7, 18)),
    ],
)
def test_supported_single_dates_are_deterministic(question, expected):
    analysis = analyze_annual_leave_input(question, business_date=BUSINESS_DATE)
    assert analysis.start_date == expected
    assert analysis.end_date == expected


def test_yearless_date_rolls_to_next_year_when_past():
    analysis = analyze_annual_leave_input(
        "申请7月15日一天年假，原因为私事", business_date=BUSINESS_DATE
    )
    assert analysis.start_date == date(2027, 7, 15)


def test_two_dates_preserve_source_order_as_range():
    analysis = analyze_annual_leave_input(
        "申请2026-07-20至2026-07-22年假，原因为家庭事务",
        business_date=BUSINESS_DATE,
    )
    assert analysis.date_evidence == ["2026-07-20", "2026-07-22"]
    assert analysis.start_date == date(2026, 7, 20)
    assert analysis.end_date == date(2026, 7, 22)


@pytest.mark.parametrize("question", ["申请一天年假，原因为私事", "申请半天年假，原因为私事"])
def test_duration_words_are_not_date_evidence(question):
    analysis = analyze_annual_leave_input(question, business_date=BUSINESS_DATE)
    assert analysis.date_evidence == []
    assert analysis.missing_fields == ["start_date", "end_date"]


def test_explicit_reason_is_extracted_without_lead_in():
    analysis = analyze_annual_leave_input(
        "申请明天一天年假，因为需要就医。", business_date=BUSINESS_DATE
    )
    assert analysis.reason_evidence == "需要就医"


def test_missing_reason_is_deterministic():
    analysis = analyze_annual_leave_input(
        "申请2026-07-20一天年假", business_date=BUSINESS_DATE
    )
    assert analysis.missing_fields == ["reason"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("申请2026-07-20上午半天年假，原因为就医", "AM"),
        ("申请2026-07-20下午半天年假，原因为就医", "PM"),
    ],
)
def test_explicit_half_day_is_deterministic(question, expected):
    assert analyze_annual_leave_input(
        question, business_date=BUSINESS_DATE
    ).half_day == expected


def test_missing_field_order_is_stable():
    analysis = analyze_annual_leave_input("申请一天年假", business_date=BUSINESS_DATE)
    assert analysis.missing_fields == ["start_date", "end_date", "reason"]


def test_more_than_two_dates_and_ambiguous_half_day_are_rejected():
    with pytest.raises(AnnualLeaveInputError):
        analyze_annual_leave_input(
            "申请2026-07-20、2026-07-21、2026-07-22年假，原因为私事",
            business_date=BUSINESS_DATE,
        )
    with pytest.raises(AnnualLeaveInputError):
        analyze_annual_leave_input(
            "申请2026-07-20上午和下午年假，原因为私事",
            business_date=BUSINESS_DATE,
        )
