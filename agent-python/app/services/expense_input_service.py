"""expense_input_service.py —— 报销输入抽取（V2 §十三 / 追加约束 §4）

LLM 只允许负责 ExpenseInputExtraction（用户问题中"想报销哪次出差 / 哪些
发票"的抽取）；cost_center / claimed_amount / reimbursable_amount / 验真状态 /
policy cap 全部由 trusted Tool Facts + deterministic 逻辑计算，禁止 LLM 组装
Proposal 业务字段。

第一版使用规则抽取（与 annual_leave_input_service 一致的确定性风格），
不调用 LLM：
- 从 question 提取"报销/报账"业务词命中（意图判定）
- 从成功 travel_record observation 中按用户显式提到的目的地 / trip_id 匹配 trip
- 缺 trip_id 或发票明细时返回固定顺序 missing_fields（V2 §十五）
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# 报销意图动作词（"报销""报账""报销一下"）。咨询句（"报销流程是什么"）由
# Planner 路由到 rag_answer_tool，不会进入 proposal 链路。
_CLAIM_PATTERN = re.compile(r"(?:报销|报账|报一下|帮我报)")

_QUERY_NOISE_EXPRESSIONS = (
    "流程", "制度", "规定", "政策", "标准", "多少", "怎么", "如何",
    "需要什么", "材料", "手续",
)

_TRIP_ID_PATTERN = re.compile(r"TRIP-[0-9A-Za-z-]+")


class ExpenseInputError(ValueError):
    pass


class ExpenseInputAnalysis(BaseModel):
    """用户输入抽取结果（规则版，确定性强）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    is_claim_intent: bool
    trip_id: str | None
    invoice_ids: list[str]
    missing_fields: list[Literal["trip_id", "expense_items", "invoice_ids"]]


def is_expense_claim_intent(question: str) -> bool:
    normalized = question.strip()
    if _CLAIM_PATTERN.search(normalized) is None:
        return False
    if any(expression in normalized for expression in _QUERY_NOISE_EXPRESSIONS):
        return False
    return True


def extract_trip_reference(question: str) -> str | None:
    """从问题里抽取 trip_id 引用；没有显式 trip_id 时返回 None（走 Clarification）。"""
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

    # 用户未显式给 trip_id 但问题含目的地：从 APPROVED trips 按目的地匹配（确定性）
    if trip_id is None:
        for trip in trips:
            destination = trip.get("destination", "")
            if destination and destination in normalized:
                trip_id = trip.get("trip_id")
                break

    # 确定 trip 后：如果用户未显式给 INV 引用，从 trip 的 expense_documents 按
    # 用户提到的类别（酒店/打车/高铁等）确定性匹配。
    if invoice_ids:
        # 只保留用户显式提到的发票（必须已验真，否则走 Clarification）
        user_invoice_ids = [inv_id for inv_id in invoice_ids]
        invoice_ids = user_invoice_ids
    elif trip_id is not None:
        matched_trip = next((trip for trip in trips if trip.get("trip_id") == trip_id), None)
        if matched_trip:
            docs = matched_trip.get("expense_documents", []) or []
            implied_invoice_ids = [
                doc.get("invoice_id")
                for doc in docs
                if doc.get("invoice_id") and _category_match(doc, normalized)
            ]
            invoice_ids = [inv_id for inv_id in implied_invoice_ids if inv_id]

    missing_fields: list[Literal["trip_id", "expense_items", "invoice_ids"]] = []
    if trip_id is None:
        missing_fields.append("trip_id")
    # 存在性判定：声明的发票必须已在 context 中验真成功
    if set(invoice_ids) and not set(invoice_ids).issubset(
            {inv.get("invoice_id") for inv in verified_invoices}):
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


def clarification_question(missing_fields: list[str]) -> str:
    if set(missing_fields) >= {"trip_id"}:
        return "请提供具体的出差记录（trip_id）或出差目的地。"
    if "invoice_ids" in missing_fields or "expense_items" in missing_fields:
        return "请提供需要报销的发票（invoice_id）。"
    return "请提供出差记录与待报销发票信息。"
