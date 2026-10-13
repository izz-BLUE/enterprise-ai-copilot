"""Trip 选择只依据当前 APPROVED facts；歧义不随接口返回顺序消失。"""

import pytest

from app.services.expense_input_service import (
    ExpenseProposalContextLike,
    analyze_expense_input,
    trip_clarification_question,
)


def trip(trip_id, start, end, *, status="APPROVED", destination="上海"):
    return {"trip_id": trip_id, "destination": destination, "start_date": start,
            "end_date": end, "status": status,
            "expense_documents": [{"invoice_id": f"INV-{trip_id}", "category": "TAXI"}]}


FIRST = trip("T001", "2026-10-01", "2026-10-03")
SECOND = trip("T002", "2026-10-20", "2026-10-22")
QUESTION = "帮我报销上海出差的全部发票，原因客户拜访"


def analyze(question=QUESTION, trips=None):
    return analyze_expense_input(question, context=ExpenseProposalContextLike({
        "travel_record": trips if trips is not None else [FIRST, SECOND],
        "invoices": [],
    }))


def test_unique_approved_destination_selects_automatically():
    result = analyze(trips=[FIRST, {**SECOND, "status": "PENDING"}])
    assert result.trip_id == "T001"
    assert result.invoice_ids == ["INV-T001"]
    assert result.trip_candidates == []


def test_duplicate_destination_requires_clarification_with_all_selection_metadata():
    result = analyze()
    assert result.trip_id is None
    assert "trip_id" in result.missing_fields
    assert result.invoice_ids == []
    assert [candidate.model_dump() for candidate in result.trip_candidates] == [
        {key: value for key, value in record.items()
         if key in {"trip_id", "destination", "start_date", "end_date"}}
        for record in [FIRST, SECOND]
    ]
    question = trip_clarification_question(result)
    for record in [FIRST, SECOND]:
        for key in ("trip_id", "destination", "start_date", "end_date"):
            assert record[key] in question


def test_candidate_analysis_and_display_ignore_provider_return_order():
    first = analyze(trips=[FIRST, SECOND])
    second = analyze(trips=[SECOND, FIRST])
    assert first == second
    assert trip_clarification_question(first) == trip_clarification_question(second)


@pytest.mark.parametrize("trip_id", ["T001", "T002", "TRIP-20261020-002", "OA.CN/002"])
def test_explicit_id_selects_exact_approved_trip_before_latest_and_destination(trip_id):
    selected = {**FIRST, "trip_id": trip_id}
    result = analyze(f"帮我报销 {trip_id}，上海最近一次出差的全部发票",
                     [{**SECOND, "trip_id": "T999"}, selected])
    assert result.trip_id == trip_id


@pytest.mark.parametrize("trip_id,status", [("T003", "APPROVED"), ("T002", "PENDING"), ("T002", "REJECTED")])
def test_unknown_or_unapproved_explicit_id_does_not_fall_back_to_destination(trip_id, status):
    result = analyze(f"帮我报销上海出差 {trip_id}", [FIRST, {**SECOND, "status": status}])
    assert result.trip_id is None
    assert "trip_id" in result.missing_fields


@pytest.mark.parametrize("expression", ["最近一次", "最新一次", "最近", "最新"])
@pytest.mark.parametrize("reverse", [False, True])
def test_latest_approved_uses_existing_end_start_id_rule(expression, reverse):
    records = [FIRST, SECOND, trip("T003", "2026-11-01", "2026-11-03", status="PENDING")]
    if reverse:
        records.reverse()
    assert analyze(f"帮我报销{expression}出差", records).trip_id == "T002"


def test_latest_keeps_existing_end_date_start_date_and_id_tie_break():
    records = [trip("T002", "2026-10-19", "2026-10-22"), SECOND,
               trip("T003", "2026-10-20", "2026-10-22")]
    assert analyze("帮我报销最近一次出差", records).trip_id == "T003"


@pytest.mark.parametrize("answer,trip_id", [
    ("T001", "T001"), ("第一条", "T001"), ("10月1号那次", "T001"),
    ("第二条", "T002"), ("2026-10-20那次", "T002"), ("最近一次", "T002"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_clarification_selection_resolves_deterministically(answer, trip_id, reverse):
    assert analyze().trip_id is None
    records = [SECOND, FIRST] if reverse else [FIRST, SECOND]
    result = analyze(f"{QUESTION}\n补充信息：{answer}", records)
    assert result.trip_id == trip_id
    assert "trip_id" not in result.missing_fields
    assert result.invoice_ids == [f"INV-{trip_id}"]
    assert result.missing_fields == ["invoice_ids"]  # 仍要求当前发票验真，不提升选择为业务事实。


@pytest.mark.parametrize("answer", ["第三条", "10月5号那次", "T001 和 T002"])
def test_invalid_or_multiple_choices_remain_clarification(answer):
    assert analyze(f"{QUESTION}\n补充信息：{answer}").trip_id is None


def test_same_month_day_across_years_is_not_unique():
    records = [FIRST, trip("T003", "2025-10-01", "2025-10-03")]
    result = analyze(f"{QUESTION}\n补充信息：10月1号那次", records)
    assert result.trip_id is None
    assert len(result.trip_candidates) == 2


def test_latest_clarification_answer_supersedes_invalid_previous_answer():
    result = analyze(f"{QUESTION}\n补充信息：T009\nT001")
    assert result.trip_id == "T001"


def test_invoice_date_does_not_change_unique_destination_selection():
    assert analyze(f"{QUESTION}，发票日期为2026-09-12", [FIRST]).trip_id == "T001"


def test_date_in_invoice_clarification_does_not_resolve_trip_ambiguity():
    assert analyze(f"{QUESTION}\n补充信息：发票日期是10月1号").trip_id is None
