"""expense_input_service.py —— 报销输入抽取（V2 §十三 / 追加约束 §4）

Planner / LLM 只允许负责报销原因的语义抽取；本服务只负责 ExpenseInputExtraction（用户问题中"想报销哪次出差 / 哪些
发票"的抽取）；cost_center / claimed_amount / reimbursable_amount / 验真状态 /
policy cap 全部由 trusted Tool Facts + deterministic 逻辑计算，禁止 LLM 组装
Proposal 业务字段。

本服务使用规则抽取（与 annual_leave_input_service 一致的确定性风格），
不调用 LLM：
- 从 question 提取"报销/报账"业务词命中（意图判定）
- 从成功 travel_record observation 中按显式 trip_id / 目的地匹配 trip
- 支持主 Demo 的确定性相对语义："最近/最新"从 APPROVED 集合选择最新 trip
- "对应发票/相关发票"只从已选 trip 的 expense_documents 推导，并仍要求
  invoice_verify 成功，不能绕过验真事实
- 缺 trip_id 或发票明细时返回固定顺序 missing_fields（V2 §十五）
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# 报销意图动作词（"报销""报账""报销一下"）。咨询句（"报销流程是什么"）由
# Planner 路由到 rag_answer_tool，不会进入 proposal 链路。
_CLAIM_PATTERN = re.compile(r"(?:报销|报账|报一下|帮我报|帮我把[^，。！？\n]*报)")
# 已有 Expense 状态 Tool 以 EXP-* 报销单号查询；这类已存在单据引用不是
# 新建报销申请 intent，避免被业务 Proposal capability preflight 拦截。
_EXPENSE_ID_PATTERN = re.compile(r"EXP-[0-9A-Za-z-]+")

_QUERY_NOISE_EXPRESSIONS = (
    "流程", "制度", "规定", "政策", "标准", "多少", "怎么", "如何",
    "需要什么", "材料", "手续", "应该填", "填什么", "填写什么", "怎么填",
    "如何填",
)

_TRIP_ID_PATTERN = re.compile(r"TRIP-[0-9A-Za-z-]+")

# find_trip_records 已经只返回 APPROVED 记录，因此相对选择可以安全地支持
# “最近这次出差”，不会误选仍处于 PENDING 的记录。
_LATEST_TRIP_EXPRESSIONS = ("最近一次", "最近的", "最近", "最新一次", "最新的", "最新")
_CORRESPONDING_INVOICE_EXPRESSIONS = (
    "对应发票", "对应的发票", "相关发票", "相关的发票", "对应费用", "对应的费用",
    "全部发票", "所有发票", "全部费用", "所有费用",
)

class ExpenseInputError(ValueError):
    pass


class ExpenseInputAnalysis(BaseModel):
    """用户输入中的出差 / 发票引用抽取结果（规则版，确定性强）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    is_claim_intent: bool
    trip_id: str | None
    invoice_ids: list[str]
    missing_fields: list[Literal["trip_id", "expense_items", "invoice_ids"]]


def is_expense_claim_intent(question: str) -> bool:
    normalized = question.strip()
    if _EXPENSE_ID_PATTERN.search(normalized) is not None:
        return False
    if _CLAIM_PATTERN.search(normalized) is None:
        return False
    if any(expression in normalized for expression in _QUERY_NOISE_EXPRESSIONS):
        return False
    return True


def extract_trip_reference(question: str) -> str | None:
    """从问题里抽取 trip_id 引用；没有显式 trip_id 时返回 None。"""
    match = _TRIP_ID_PATTERN.search(question)
    return match.group(0) if match else None


def extract_invoice_references(question: str) -> list[str]:
    """从问题里抽取 INV-xxx 引用（用户在问题里明确提到的发票号）。"""
    return re.findall(r"INV-[0-9A-Za-z-]+", question)


def find_trip_records(context: ExpenseProposalContextLike) -> list[dict[str, Any]]:
    """从 ExpenseProposalContext.travel_record 中定位可报销 trip。

    返回全部 APPROVED trip；确定性筛选规则：status == 'APPROVED'。
    """
    trips = context.travel_record if context.has_key("travel_record") else []
    return [trip for trip in trips if str(trip.get("status", "")).upper() == "APPROVED"]


def find_invoice_records(context: ExpenseProposalContextLike) -> list[dict[str, Any]]:
    """从 ExpenseProposalContext.invoices 中提取验真成功的发票记录。"""
    invoices = context.invoices if context.has_key("invoices") else []
    return [
        invoice
        for invoice in invoices
        if invoice.get("success") is True and invoice.get("valid") is True
    ]


class ExpenseProposalContextLike:
    """duck-typed 视图：允许 dict 或对象（expense_schema.ExpenseProposalContext）。"""

    def __init__(self, value: Any):
        self._value = value

    def has_key(self, key: str) -> bool:
        if isinstance(self._value, dict):
            return key in self._value
        return hasattr(self._value, key) and getattr(self._value, key) is not None

    @property
    def travel_record(self) -> list:
        if isinstance(self._value, dict):
            return self._value.get("travel_record", [])
        return list(getattr(self._value, "travel_record", []) or [])

    @property
    def invoices(self) -> list:
        if isinstance(self._value, dict):
            return self._value.get("invoices", [])
        return list(getattr(self._value, "invoices", []) or [])

    @property
    def policy_context(self) -> str:
        if isinstance(self._value, dict):
            return self._value.get("policy_context", "")
        return str(getattr(self._value, "policy_context", "") or "")


def _wants_latest_trip(question: str) -> bool:
    lowered = question.lower()
    return any(expression in lowered for expression in _LATEST_TRIP_EXPRESSIONS)


def _latest_approved_trip(trips: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从已过滤为 APPROVED 的 trips 中确定性选择最新记录。

    fixture/业务接口日期均为 ISO-8601 YYYY-MM-DD，因此字符串排序与日期排序一致。
    先按 end_date，再按 start_date、trip_id 做稳定 tie-break；缺字段按空字符串处理。
    """
    if not trips:
        return None
    return max(
        trips,
        key=lambda trip: (
            str(trip.get("end_date") or ""),
            str(trip.get("start_date") or ""),
            str(trip.get("trip_id") or ""),
        ),
    )


def _wants_corresponding_invoices(question: str) -> bool:
    lowered = question.lower()
    return any(expression in lowered for expression in _CORRESPONDING_INVOICE_EXPRESSIONS)


def analyze_expense_input(
    question: str,
    *,
    context: ExpenseProposalContextLike,
) -> ExpenseInputAnalysis:
    normalized = question.strip()
    if not is_expense_claim_intent(normalized):
        raise ExpenseInputError("not_expense_claim_intent")

    trip_id = extract_trip_reference(normalized)
    invoice_ids = extract_invoice_references(normalized)
    trips = find_trip_records(context)
    verified_invoices = find_invoice_records(context)

    # 用户未显式给 trip_id 时，先处理相对选择语义。find_trip_records
    # 已经完成 APPROVED 过滤，因此“最近/最新”不会选择 PENDING。
    if trip_id is None and _wants_latest_trip(normalized):
        latest_trip = _latest_approved_trip(trips)
        if latest_trip is not None:
            candidate = latest_trip.get("trip_id")
            if isinstance(candidate, str) and candidate:
                trip_id = candidate

    # 仍未确定 trip 时，再按用户显式目的地匹配 APPROVED trip（保持旧行为）。
    if trip_id is None:
        for trip in trips:
            destination = trip.get("destination", "")
            if destination and destination in normalized:
                trip_id = trip.get("trip_id")
                break

    # 确定 trip 后：
    # 1. 用户显式给 INV → 原样保留，后续必须通过 verified_invoices 检查；
    # 2. 用户说“对应/相关/全部发票” → 取该 trip 的全部 expense_documents；
    # 3. 否则保持旧规则，按用户提到的费用类别匹配。
    if invoice_ids:
        invoice_ids = list(invoice_ids)
    elif trip_id is not None:
        matched_trip = next((trip for trip in trips if trip.get("trip_id") == trip_id), None)
        if matched_trip:
            docs = matched_trip.get("expense_documents", []) or []
            if (
                _wants_corresponding_invoices(normalized)
                or not _mentions_expense_category(normalized)
            ):
                implied_invoice_ids = [
                    doc.get("invoice_id")
                    for doc in docs
                    if doc.get("invoice_id")
                ]
            else:
                implied_invoice_ids = [
                    doc.get("invoice_id")
                    for doc in docs
                    if doc.get("invoice_id") and _category_match(doc, normalized)
                ]
            invoice_ids = [inv_id for inv_id in implied_invoice_ids if inv_id]

    missing_fields: list[Literal["trip_id", "expense_items", "invoice_ids"]] = []
    if trip_id is None:
        missing_fields.append("trip_id")

    # 所有自然语言推导出的 invoice_ids 仍必须已经由 invoice_verify_tool 成功验真；
    # 相对选择/“对应发票”仅用于选择，不提升任何业务事实可信度。
    verified_invoice_ids = {
        inv.get("invoice_id") for inv in verified_invoices if inv.get("invoice_id")
    }
    if set(invoice_ids) and not set(invoice_ids).issubset(verified_invoice_ids):
        missing_fields.append("invoice_ids")
    elif not invoice_ids:
        missing_fields.append("invoice_ids")
    if not invoice_ids:
        missing_fields.append("expense_items")

    return ExpenseInputAnalysis(
        is_claim_intent=True,
        trip_id=trip_id,
        invoice_ids=invoice_ids,
        missing_fields=missing_fields,
    )


# 用户自然语言 → 费用类别关键词映射（确定性抽取，V2 §十四 demo 规则）
_CATEGORY_KEYWORDS = {
    "hotel": ("酒店", "住宿", "宾馆", "旅馆", "hotel"),
    "taxi": ("打车", "出租车", "出租", "taxi", "交通"),
    "train": ("高铁", "火车", "动车", "列车", "train"),
    "flight": ("机票", "航班", "飞机", "flight"),
    "meal": ("餐饮", "餐费", "吃饭", "餐", "meal", "晚宴"),
}


def _category_match(doc: dict, question: str) -> bool:
    """doc(category=CATEGORY) 是否与用户问题提到的类别关键词匹配。"""
    category = str(doc.get("category", "")).upper()
    lowered_question = question.lower()
    for cfg_category, keywords in _CATEGORY_KEYWORDS.items():
        if cfg_category == category.lower():
            return any(keyword in lowered_question for keyword in keywords)
    return True  # 未映射类别默认包含（保守放行，留给后续验真）


def _mentions_expense_category(question: str) -> bool:
    lowered_question = question.lower()
    return any(
        keyword in lowered_question
        for keywords in _CATEGORY_KEYWORDS.values()
        for keyword in keywords
    )


def clarification_question(missing_fields: list[str]) -> str:
    if set(missing_fields) >= {"trip_id"}:
        return "请提供具体的出差记录（trip_id）或出差目的地。"
    if "invoice_ids" in missing_fields or "expense_items" in missing_fields:
        return "请提供需要报销的发票（invoice_id）。"
    return "请提供出差记录与待报销发票信息。"
