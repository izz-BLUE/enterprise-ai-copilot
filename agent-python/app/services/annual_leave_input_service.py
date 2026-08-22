import re
import unicodedata
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

# 意图动作词正则：动作词必须出现在"年假"之前，允许 0..14 个非标点字符
# （日期状语等）插入——"帮我请明天一天年假""请5月20日到5月22日年假"均能命中；
# "年假能请几天"这类咨询句因动作词在"年假"之后，天然不命中。
_ACTION_PATTERN = re.compile(
    r"(?:申请|我要请|我想请|帮我请|帮我休|要请|想请|打算请|请|休)"
    r"[^，。；,\n]{0,14}年假"
)
# 咨询引导词：命中即视为知识咨询（"请问怎么申请年假"），不视为业务动作请求。
# 只放引导句式，不放内容词（如"材料"），避免误伤"帮我请年假，因为要准备材料"。
_QUERY_NOISE_EXPRESSIONS = (
    "请问", "咨询", "想了解", "了解下", "怎么请", "如何请",
    "怎么申请", "如何申请", "需要什么", "需要哪些",
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
_HALF_DAY_WORD = "半天"


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
    missing_fields: list[Literal["start_date", "end_date", "reason", "half_day"]]


def is_annual_leave_action_intent(question: str) -> bool:
    normalized = question.strip()
    if "年假" not in normalized:
        return False
    if any(expression in normalized for expression in KNOWLEDGE_EXPRESSIONS):
        return False
    if any(expression in normalized for expression in _QUERY_NOISE_EXPRESSIONS):
        return False
    return _ACTION_PATTERN.search(normalized) is not None


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

    # 原因子句（因为/由于/为了/原因为…到标点）内的日期、上午/下午、半天等
    # 业务词属于原因内容，不属于申请字段；把原因区间替换为空格生成 scan_text，
    # 日期证据与半天语义只从 scan_text 提取，避免"5月21日请年假，因为5月20日
    # 有婚礼"把原因日期误当申请日期。
    reason_match = _REASON_PATTERN.search(normalized)
    reason_evidence = reason_match.group(1).strip() if reason_match else ""
    if reason_evidence and (
        len(reason_evidence) > 200
        or any(unicodedata.category(character) == "Cc" for character in reason_evidence)
    ):
        raise AnnualLeaveInputError("invalid_reason")
    if reason_match:
        masked_chars = list(normalized)
        reason_start, reason_end = reason_match.span(1)
        for index in range(reason_start, reason_end):
            masked_chars[index] = " "
        scan_text = "".join(masked_chars)
    else:
        scan_text = normalized

    date_evidence = [match.group(0) for match in _DATE_PATTERN.finditer(scan_text)]
    if len(date_evidence) > 2:
        raise AnnualLeaveInputError("too_many_dates")
    parsed_dates = [
        parse_date_evidence(evidence, business_date=business_date)
        for evidence in date_evidence
    ]
    start_date = parsed_dates[0] if parsed_dates else None
    end_date = parsed_dates[-1] if parsed_dates else None

    has_am = any(expression in scan_text for expression in _AM_EXPRESSIONS)
    has_pm = any(expression in scan_text for expression in _PM_EXPRESSIONS)
    if has_am and has_pm:
        raise AnnualLeaveInputError("ambiguous_half_day")
    half_day: Literal["NONE", "AM", "PM"] = "AM" if has_am else "PM" if has_pm else "NONE"
    # 出现"半天"但未指明上午/下午 → 语义歧义：走 Clarification 追问，
    # 不再静默按全天生成草稿（"申请8月25日半天年假"原本会生成全天 Proposal）。
    half_day_ambiguous = half_day == "NONE" and _HALF_DAY_WORD in scan_text

    missing_fields: list[Literal["start_date", "end_date", "reason", "half_day"]] = []
    if start_date is None:
        missing_fields.append("start_date")
    if end_date is None:
        missing_fields.append("end_date")
    if not reason_evidence:
        missing_fields.append("reason")
    if half_day_ambiguous:
        missing_fields.append("half_day")

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
    if "half_day" in missing_fields:
        parts = []
        if set(missing_fields) & {"start_date", "end_date"}:
            parts.append("明确年假日期")
        if "reason" in missing_fields:
            parts.append("申请原因")
        parts.append("明确上午还是下午半天")
        return "请补充" + "、".join(parts) + "。"
    return "请补充明确的年假日期和申请原因。"
