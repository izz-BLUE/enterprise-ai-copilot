"""expense_calculation_service.py —— 报销金额确定性计算（V2 §十一 / §十四）

不是 Planner Tool；只在 expense_proposal_tool 内部调用。
V1 固定 demo 规则（与 RAG 政策知识相互独立 —— RAG 只提供知识/解释上下文，
金额与限额判断的最终权威规则写在确定性业务代码里，禁止解析 RAG 自然语言）：
- HOTEL（酒店）：每晚最高 750，按天数 × 750 封顶
- TAXI（市内交通）：合法发票实报
- TRAIN / FLIGHT（高铁/机票）：合法凭证实报
- MEAL（餐饮）：合法发票实报（demo fixture 不做单独限额）

相同输入 → 相同输出（禁止 LLM 计算金额）。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

HOTEL_NIGHTLY_CAP = Decimal("750")


def claimed_amount(items: list[dict[str, Any]]) -> Decimal:
    """申报总额 = 所有 item.amount 之和（保留 2 位）。"""
    total = sum(_as_decimal(item.get("amount", 0)) for item in items)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def reimbursable_amount(
    items: list[dict[str, Any]],
    stay_nights: int,
) -> Decimal:
    """实报总额：HOTEL 按 750×晚封顶，其它合法实报。"""
    nights = max(int(stay_nights or 1), 1)
    cap = HOTEL_NIGHTLY_CAP * Decimal(nights)
    total = Decimal("0")
    for item in items:
        amount = _as_decimal(item.get("amount", 0))
        category = str(item.get("category", "")).upper()
        if category == "HOTEL":
            total += min(amount, cap)
        else:
            total += amount
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def infer_stay_nights(trip: dict[str, Any] | None) -> int:
    """从 travel_record 的 start_date/end_date 确定性计算晚数（end - start）。

    计算不出时不返回 0 —— 缺省 1 晚兜底（与 Java ExpenseCalculationService 对齐）。
    """
    if not trip:
        return 1
    start = trip.get("start_date")
    end = trip.get("end_date")
    if not start or not end:
        return 1
    try:
        from datetime import date

        start_d = date.fromisoformat(str(start))
        end_d = date.fromisoformat(str(end))
        nights = (end_d - start_d).days
        return nights if nights >= 1 else 1
    except (ValueError, TypeError):
        return 1


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return Decimal(str(value))
