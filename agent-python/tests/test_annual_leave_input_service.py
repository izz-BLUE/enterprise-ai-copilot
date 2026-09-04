from datetime import date

import pytest

from app.services.annual_leave_input_service import (
    AnnualLeaveInputError,
    analyze_annual_leave_input,
    clarification_question,
    is_annual_leave_action_intent,
    is_personal_annual_leave_balance_query,
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
        # 插入状语 / 口语化句式（旧子串匹配漏检，正则覆盖）
        "帮我请明天一天年假",
        "明天请一天年假",
        "我要休年假",
        "帮我请三天年假，因为要回老家",
        "麻烦帮我请一下年假",
        "帮我请5月20日到5月22日年假",
        "帮我申请下周一整周年假",
        "帮我请明天年假，因为要准备材料",
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
        # 咨询引导句式不得误判为业务动作
        "请问怎么申请年假",
        "请年假需要什么材料",
        "年假能请几天",
        "我想了解年假",
        "请问年假能请几天",
    ],
)
def test_policy_and_balance_questions_remain_rag(question):
    assert not is_annual_leave_action_intent(question)


@pytest.mark.parametrize(
    ('question', 'expected'),
    [
        ('查询我的年假余额', True),
        ('请查询我的年假余额', True),
        ('我的年假余额是多少', True),
        ('我还有多少年假', True),
        ('我还剩多少天年假', True),
        ('看一下我的年假余额', True),
        ('查一下我今年还剩多少年假', True),
        ('公司的年假制度是什么', False),
        ('年假余额怎么计算', False),
        ('年假怎么结转', False),
        ('年假能请多少天', False),
        ('入职一年有多少天年假', False),
        ('帮我请明天一天年假', False),
        ('怎么申请年假', False),
        ('查询我的年假余额以及公司的年假计算规则', False),
        ('查询我的年假余额以及公司的年假规则', False),
        ('我的年假余额怎么查', False),
    ],
)
def test_personal_annual_leave_balance_query_is_narrow(question, expected):
    assert is_personal_annual_leave_balance_query(question) is expected


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


@pytest.mark.parametrize("question", ["申请一天年假，原因为私事"])
def test_duration_words_are_not_date_evidence(question):
    analysis = analyze_annual_leave_input(question, business_date=BUSINESS_DATE)
    assert analysis.date_evidence == []
    assert analysis.missing_fields == ["start_date", "end_date"]


def test_explicit_reason_is_extracted_without_lead_in():
    analysis = analyze_annual_leave_input(
        "申请明天一天年假，因为需要就医。", business_date=BUSINESS_DATE
    )
    assert analysis.reason_evidence == "需要就医"


@pytest.mark.parametrize(
    ("question", "expected_evidence"),
    [
        # 原因子句中的日期不得污染申请日期（原"5月21日请年假，因为5月20日有婚礼"会倒挂）
        ("申请5月21日年假，因为5月20日要参加婚礼", ["5月21日"]),
        # 原因日期不得被误当申请日期（原会错误生成 8月25日 草稿）
        ("申请年假，因为8月25日要参加婚礼", []),
        # 原因含第 3 个日期不再触发 too_many_dates
        ("申请8月25日、8月26日年假，因为8月27日要参加婚礼", ["8月25日", "8月26日"]),
        # 原因在句首同样排除
        ("因为要回老家，申请8月25日年假", ["8月25日"]),
        # 原因中的上午/半天不得影响 half_day 判定
        ("申请8月25日年假，因为上午要开半天会", ["8月25日"]),
    ],
)
def test_reason_clause_dates_are_excluded(question, expected_evidence):
    analysis = analyze_annual_leave_input(question, business_date=BUSINESS_DATE)
    assert analysis.date_evidence == expected_evidence


@pytest.mark.parametrize(
    ("question", "expected_half_day", "expected_missing"),
    [
        # 裸"半天"未指明上午/下午 → 语义歧义走澄清，不再静默生成全天草稿
        ("申请8月25日半天年假，原因为私事", "NONE", ["half_day"]),
        ("请半天年假", "NONE", ["start_date", "end_date", "reason", "half_day"]),
        # 显式上午/下午不歧义
        ("申请8月25日上午半天年假，原因为私事", "AM", []),
        ("申请8月25日下午半天年假，原因为私事", "PM", []),
    ],
)
def test_bare_half_day_is_ambiguous(question, expected_half_day, expected_missing):
    analysis = analyze_annual_leave_input(question, business_date=BUSINESS_DATE)
    assert analysis.half_day == expected_half_day
    assert analysis.missing_fields == expected_missing


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        (["reason"], "请补充年假申请原因。"),
        (["start_date", "end_date"], "请提供明确的年假日期。"),
        (["half_day"], "请补充明确上午还是下午半天。"),
        (["reason", "half_day"], "请补充申请原因、明确上午还是下午半天。"),
        (["start_date", "end_date", "reason"], "请补充明确的年假日期和申请原因。"),
    ],
)
def test_clarification_question_wording(missing, expected):
    assert clarification_question(missing) == expected


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
