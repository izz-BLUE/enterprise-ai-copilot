"""Purchase 领域的确定性本地事实源。

P4-3 只需要证明领域扩展可以消费可信事实，不接入真实采购系统。预算和政策
均由这组固定 fixture 计算；LLM 不参与事实生成。
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Sequence

AVAILABLE_PURCHASE_BUDGETS = {
    'E10001': Decimal('20000.00'),
}
DEVELOPMENT_DEVICE_BUDGET_LIMIT = Decimal('20000.00')


def available_budget_for(employee_id: str) -> Decimal | None:
    """返回当前员工的确定性可用采购预算。未知员工 fail-closed。"""
    return AVAILABLE_PURCHASE_BUDGETS.get(employee_id)


def evaluate_policy(
    item_name: str,
    requested_budget: Decimal,
    justification: str,
) -> tuple[str, str]:
    """按最小本地规则评估开发设备采购政策。"""
    if not justification.strip():
        return 'FAIL', '采购申请必须提供 justification。'
    if requested_budget <= 0:
        return 'FAIL', 'requested_budget 必须大于 0。'
    normalized = item_name.casefold()
    if not any(marker in normalized for marker in ('开发', 'mac', '电脑', '笔记本')):
        return 'FAIL', '当前 fixture 只允许开发设备采购。'
    if requested_budget > DEVELOPMENT_DEVICE_BUDGET_LIMIT:
        return 'FAIL', '开发设备单次预算不得超过 20000。'
    return 'PASS', '开发设备单次预算不超过 20000 且 justification 已提供。'


def purchase_fact_context(
    tool_history: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """只从当前 execution 成功的 Purchase fact observations 聚合事实。"""
    result: dict[str, dict[str, Any]] = {}
    for item in tool_history:
        if item.get('status') != 'success':
            continue
        tool_name = item.get('tool_name')
        if tool_name not in ('purchase_budget_tool', 'purchase_policy_tool'):
            continue
        observation = item.get('observation')
        if not isinstance(observation, str):
            continue
        try:
            payload = json.loads(observation)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get('success') is True:
            result[tool_name.removesuffix('_tool')] = payload
    return result
