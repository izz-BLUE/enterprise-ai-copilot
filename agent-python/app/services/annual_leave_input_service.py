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
_PERSONAL_BALANCE_KNOWLEDGE_EXPRESSIONS = (
    "政策", "规定", "怎么算", "计算", "结转", "流程", "制度",
    "规则", "能请", "可以请", "怎么申请", "如何申请", "怎么请", "申请",
    "怎么查", "如何查", "查询方法", "查询流程",
)
_DATE_PATTERN = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{4}年\d{1,2}月\d{1,2}[日号]"
    r"|\d{1,2}月\d{1,2}[日号]"
    r"|大后天|后天|明天|今天"
)
_REASON_PATTERN = re.compile(
    r"(?:原因为|原因是|原因[:：]?|因为|由于|为了)([^，。；;\r\n]+)"
)
_AM_EXPRESSIONS = ("上午半天", "上午", "早上")
_PM_EXPRESSIONS = ("下午半天", "下午", "午后")
_FULL_DAY_EXPRESSIONS = ("一天", "全天", "一整天")
_HALF_DAY_WORD = "半天"
_CONTINUATION_TYPE = "leave_clarification"
_CONTINUATION_MISSING_FIELDS = frozenset({
    "start_date", "end_date", "reason", "half_day",
})
_CONTINUATION_WAITING_FOR = frozenset({"date", "reason", "half_day"})
_NON_REASON_EXPRESSIONS = (
    "请问", "怎么", "如何", "什么", "多少", "是否", "能否", "年假",
    "请假", "报销", "出差", "发票", "费用", "制度", "政策", "余额",
    "流程", "申请", "提交", "取消", "算了", "放弃", "不用", "不需要",
    "你好", "谢谢", "再见", "cancel",
)


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
    half_day_ambiguous: bool = False


def _validate_reason_evidence(reason: str) -> str:
    reason = reason.strip()
    if reason and (
        len(reason) > 200
        or any(unicodedata.category(character) == "Cc" for character in reason)
    ):
        raise AnnualLeaveInputError("invalid_reason")
    return reason


def _parse_continuation_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def normalize_leave_continuation_state(state: object) -> dict | None:
    """校验并规范化 Memory 中的 Leave clarification 槽位。

    Memory 是不可信历史数据；这里只接受带明确类型标记、绝对 ISO 日期和
    有限枚举的结构，不从 summary 或 raw original_request 推断业务字段。
    """
    if not isinstance(state, dict) or state.get("continuation_type") != _CONTINUATION_TYPE:
        return None
    # task_state 允许同时保留 Memory Extractor 的通用字段；这里只消费并
    # 规范化下方 Leave continuation 白名单字段，未知字段不会进入业务解析。
    start_date = _parse_continuation_date(state.get("start_date"))
    end_date = _parse_continuation_date(state.get("end_date"))
    if state.get("start_date") is not None and start_date is None:
        return None
    if state.get("end_date") is not None and end_date is None:
        return None
    if start_date and end_date and start_date > end_date:
        return None

    half_day = state.get("half_day")
    if half_day not in (None, "NONE", "AM", "PM"):
        return None
    reason = state.get("reason")
    if reason is not None and not isinstance(reason, str):
        return None
    try:
        reason = _validate_reason_evidence(reason or "") or None
    except AnnualLeaveInputError:
        return None

    missing_fields = state.get("missing_fields")
    if (
        not isinstance(missing_fields, list)
        or not missing_fields
        or any(field not in _CONTINUATION_MISSING_FIELDS for field in missing_fields)
        or len(set(missing_fields)) != len(missing_fields)
    ):
        return None
    waiting_for = state.get("waiting_for")
    if waiting_for not in _CONTINUATION_WAITING_FOR:
        return None
    expected_missing_fields = []
    if start_date is None:
        expected_missing_fields.append("start_date")
    if end_date is None:
        expected_missing_fields.append("end_date")
    if not reason:
        expected_missing_fields.append("reason")
    if half_day is None:
        expected_missing_fields.append("half_day")
    if missing_fields != expected_missing_fields or waiting_for != _waiting_for(missing_fields):
        return None
    return {
        "continuation_type": _CONTINUATION_TYPE,
        "start_date": start_date,
        "end_date": end_date,
        "half_day": half_day,
        "reason": reason,
        "waiting_for": waiting_for,
        "missing_fields": list(missing_fields),
    }


def serialize_leave_continuation_state(state: object) -> dict | None:
    """把已校验槽位转换为可安全写入 Memory / Checkpoint 的 JSON 结构。"""
    normalized = normalize_leave_continuation_state(state)
    if normalized is None:
        return None
    return {
        **normalized,
        "start_date": (
            normalized["start_date"].isoformat()
            if normalized["start_date"] else None
        ),
        "end_date": (
            normalized["end_date"].isoformat()
            if normalized["end_date"] else None
        ),
    }


def _waiting_for(missing_fields: list[str]) -> str:
    if "start_date" in missing_fields or "end_date" in missing_fields:
        return "date"
    if "reason" in missing_fields:
        return "reason"
    return "half_day"


def build_leave_continuation_state(analysis: AnnualLeaveInputAnalysis) -> dict:
    return {
        "continuation_type": _CONTINUATION_TYPE,
        "start_date": analysis.start_date.isoformat() if analysis.start_date else None,
        "end_date": analysis.end_date.isoformat() if analysis.end_date else None,
        "half_day": None if analysis.half_day_ambiguous else analysis.half_day,
        "reason": analysis.reason_evidence or None,
        "waiting_for": _waiting_for(analysis.missing_fields),
        "missing_fields": list(analysis.missing_fields),
    }


def _is_bare_reason_candidate(value: str) -> bool:
    """判断 clarification 中不带前缀的短文本是否可作为原因。

    只在当前 ACTIVE Leave 缺 reason 时调用；问题词、知识咨询词和其它
    业务词显式排除，避免把 unrelated question 写进 Leave Memory。
    """
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        return False
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        return False
    if normalized.endswith(("?", "？")):
        return False
    if any(expression in normalized for expression in _NON_REASON_EXPRESSIONS):
        return False
    if _DATE_PATTERN.search(normalized):
        return False
    if any(expression in normalized for expression in (*_AM_EXPRESSIONS, *_PM_EXPRESSIONS)):
        return False
    if _HALF_DAY_WORD in normalized:
        return False
    return True


def is_leave_continuation_input(question: str, missing_fields: list[str]) -> bool:
    """判断当前输入是否提供了某个待补 Leave 槽位的证据。"""
    normalized = question.strip()
    if not normalized or not isinstance(missing_fields, list):
        return False
    if "reason" in missing_fields and (
        _REASON_PATTERN.search(normalized) is not None
        or _is_bare_reason_candidate(normalized)
    ):
        return True
    if set(missing_fields) & {"start_date", "end_date"} and _DATE_PATTERN.search(normalized):
        return True
    if "half_day" in missing_fields and (
        any(expression in normalized for expression in (*_AM_EXPRESSIONS, *_PM_EXPRESSIONS))
        or _HALF_DAY_WORD in normalized
    ):
        return True
    return False


def is_annual_leave_action_intent(question: str) -> bool:
    normalized = question.strip()
    if "年假" not in normalized:
        return False
    if any(expression in normalized for expression in KNOWLEDGE_EXPRESSIONS):
        return False
    if any(expression in normalized for expression in _QUERY_NOISE_EXPRESSIONS):
        return False
    return _ACTION_PATTERN.search(normalized) is not None


def is_personal_annual_leave_balance_query(question: str) -> bool:
    """判断是否为明确的本人实时年假余额查询。"""
    normalized = question.strip()
    if "年假" not in normalized:
        return False
    if any(expression in normalized for expression in _PERSONAL_BALANCE_KNOWLEDGE_EXPRESSIONS):
        return False
    if any(expression in normalized for expression in ("以及", "并且", "同时", "和")):
        return False

    has_personal_reference = any(
        expression in normalized for expression in ("我的", "本人", "个人")
    )
    has_personal_remaining = re.search(
        r"我(?:今年)?还(?:有|剩)(?:多少|几)?", normalized
    ) is not None
    has_balance_fact = any(
        expression in normalized for expression in ("余额", "剩余", "还剩", "还有多少")
    )
    if not ((has_personal_reference or has_personal_remaining) and has_balance_fact):
        return False
    # 保留现有动作判断作为最后一层保护，避免带本人词的申请句被误判为只读。
    return not is_annual_leave_action_intent(normalized)


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
    continuation_state: dict | None = None,
) -> AnnualLeaveInputAnalysis:
    normalized = question.strip()
    normalized_continuation = normalize_leave_continuation_state(continuation_state)
    if continuation_state is not None and normalized_continuation is None:
        raise AnnualLeaveInputError("invalid_continuation_state")

    # 原因子句（因为/由于/为了/原因为…到标点）内的日期、上午/下午、半天等
    # 业务词属于原因内容，不属于申请字段；把原因区间替换为空格生成 scan_text，
    # 日期证据与半天语义只从 scan_text 提取，避免"5月21日请年假，因为5月20日
    # 有婚礼"把原因日期误当申请日期。
    reason_match = _REASON_PATTERN.search(normalized)
    reason_evidence = reason_match.group(1).strip() if reason_match else ""
    reason_evidence = _validate_reason_evidence(reason_evidence)
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

    if (
        not reason_evidence
        and normalized_continuation is not None
        and "reason" in normalized_continuation["missing_fields"]
        and not date_evidence
        and not has_am
        and not has_pm
        and not half_day_ambiguous
        and _is_bare_reason_candidate(normalized)
    ):
        reason_evidence = _validate_reason_evidence(normalized)

    missing_fields: list[Literal["start_date", "end_date", "reason", "half_day"]] = []
    if start_date is None:
        missing_fields.append("start_date")
    if end_date is None:
        missing_fields.append("end_date")
    if not reason_evidence:
        missing_fields.append("reason")
    if half_day_ambiguous:
        missing_fields.append("half_day")

    if normalized_continuation is not None:
        current_has_half_day = bool(
            has_am or has_pm or half_day_ambiguous
            or any(expression in scan_text for expression in _FULL_DAY_EXPRESSIONS)
        )
        start_date = start_date or normalized_continuation["start_date"]
        end_date = end_date or normalized_continuation["end_date"]
        reason_evidence = reason_evidence or normalized_continuation["reason"] or ""
        if current_has_half_day:
            merged_half_day = None if half_day_ambiguous else half_day
        else:
            merged_half_day = normalized_continuation["half_day"]
        missing_fields = []
        if start_date is None:
            missing_fields.append("start_date")
        if end_date is None:
            missing_fields.append("end_date")
        if not reason_evidence:
            missing_fields.append("reason")
        if merged_half_day is None:
            missing_fields.append("half_day")
        half_day = merged_half_day or "NONE"

    return AnnualLeaveInputAnalysis(
        normalized_question=normalized,
        date_evidence=date_evidence,
        start_date=start_date,
        end_date=end_date,
        reason_evidence=reason_evidence,
        half_day=half_day,
        missing_fields=missing_fields,
        half_day_ambiguous=normalized_continuation is not None and merged_half_day is None
        if normalized_continuation is not None else half_day_ambiguous,
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
