import re
import unicodedata
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict


ACTION_EXPRESSIONS = (
    "申请", "我要请", "我想请", "请一天", "请半天", "休一天", "休半天",
)
KNOWLEDGE_EXPRESSIONS = (
    "政策", "规定", "多少天", "余额", "怎么算", "计算", "结转", "流程", "制度",
)
_DATE_PATTERN = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{4}年\d{1,2}月\d{1,2}[日号]"
    r"|\d{1,2}月\d{1,2}[日号]"
    r"|大后天|后天|明天|今天"
)
_REASON_PATTERN = re.compile(
    r"(?:原因为|原因是|原因[:：]|因为|由于|为了)([^，。；;\r\n]+)"
)
_AM_EXPRESSIONS = ("上午半天", "上午", "早上")
_PM_EXPRESSIONS = ("下午半天", "下午", "午后")


class AnnualLeaveInputError(ValueError):
    pass


class AnnualLeaveInputAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_question: str
    date_evidence: list[str]
    start_date: date | None
    end_date: date | None
    reason_evidence: str
    half_day: Literal["NONE", "AM", "PM"]
    missing_fields: list[Literal["start_date", "end_date", "reason"]]


def is_annual_leave_action_intent(question: str) -> bool:
    normalized = question.strip()
    if "年假" not in normalized:
        return False
    if any(expression in normalized for expression in KNOWLEDGE_EXPRESSIONS):
        return False
    return any(expression in normalized for expression in ACTION_EXPRESSIONS)


def parse_date_evidence(value: str, *, business_date: date) -> date:
    if value == "今天":
        return business_date
    if value == "明天":
        return business_date + timedelta(days=1)
    if value == "后天":
        return business_date + timedelta(days=2)
    if value == "大后天":
        return business_date + timedelta(days=3)

    normalized = value.replace("/", "-").replace(".", "-")
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if iso_match:
        return date(*(int(part) for part in iso_match.groups()))

    full_match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", value)
    if full_match:
        return date(*(int(part) for part in full_match.groups()))

    month_day_match = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]", value)
    if month_day_match:
        month, day = (int(part) for part in month_day_match.groups())
        resolved = date(business_date.year, month, day)
        if resolved < business_date:
            resolved = date(business_date.year + 1, month, day)
        return resolved
    raise AnnualLeaveInputError("unsupported_date_evidence")


def parse_half_day_evidence(value: str) -> Literal["NONE", "AM", "PM"]:
    if not value:
        return "NONE"
    has_am = any(expression in value for expression in _AM_EXPRESSIONS)
    has_pm = any(expression in value for expression in _PM_EXPRESSIONS)
    if has_am == has_pm:
        raise AnnualLeaveInputError("invalid_half_day_evidence")
    return "AM" if has_am else "PM"


def analyze_annual_leave_input(
    question: str,
    *,
    business_date: date,
) -> AnnualLeaveInputAnalysis:
    normalized = question.strip()
    date_evidence = [match.group(0) for match in _DATE_PATTERN.finditer(normalized)]
    if len(date_evidence) > 2:
        raise AnnualLeaveInputError("too_many_dates")
    parsed_dates = [
        parse_date_evidence(evidence, business_date=business_date)
        for evidence in date_evidence
    ]
    start_date = parsed_dates[0] if parsed_dates else None
    end_date = parsed_dates[-1] if parsed_dates else None

    reason_match = _REASON_PATTERN.search(normalized)
    reason_evidence = reason_match.group(1).strip() if reason_match else ""
    if reason_evidence and (
        len(reason_evidence) > 200
        or any(unicodedata.category(character) == "Cc" for character in reason_evidence)
    ):
        raise AnnualLeaveInputError("invalid_reason")

    has_am = any(expression in normalized for expression in _AM_EXPRESSIONS)
    has_pm = any(expression in normalized for expression in _PM_EXPRESSIONS)
    if has_am and has_pm:
        raise AnnualLeaveInputError("ambiguous_half_day")
    half_day: Literal["NONE", "AM", "PM"] = "AM" if has_am else "PM" if has_pm else "NONE"

    missing_fields: list[Literal["start_date", "end_date", "reason"]] = []
    if start_date is None:
        missing_fields.append("start_date")
    if end_date is None:
        missing_fields.append("end_date")
    if not reason_evidence:
        missing_fields.append("reason")

    return AnnualLeaveInputAnalysis(
        normalized_question=normalized,
        date_evidence=date_evidence,
        start_date=start_date,
        end_date=end_date,
        reason_evidence=reason_evidence,
        half_day=half_day,
        missing_fields=missing_fields,
    )


def clarification_question(missing_fields: list[str]) -> str:
    if missing_fields == ["reason"]:
        return "请补充年假申请原因。"
    if set(missing_fields).issubset({"start_date", "end_date"}):
        return "请提供明确的年假日期。"
    return "请补充明确的年假日期和申请原因。"
