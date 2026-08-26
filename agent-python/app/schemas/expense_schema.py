"""expense_schema.py —— P2-A Expense Workflow V1 数据模型

V2 §十五：
- ExpenseActionProposal：strict / extra='forbid'；action_type Literal["EXPENSE_CLAIM"]；
  禁止携带 trusted identity 字段（employee_id / user_id / role / permission /
  token / nonce / idempotency_key）
- ExpenseInputExtraction：LLM 只负责抽取输入（trip_id / 目标发票），
  业务字段（cost_center / claimed_amount / reimbursable_amount / 验真状态 /
  policy cap）由 trusted Tool Facts + deterministic 逻辑计算（追加约束 §4）
- ExpenseProposalContext：由程序层从当前请求成功 tool_history 确定性构造，
  不允许把 raw tool_history 交给 LLM 解析（追加约束 §1/§2）
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ExpenseActionProposal(BaseModel):
    """报销业务动作 Proposal（Python → Java 内部契约）。

    action_type 固定 Literal["EXPENSE_CLAIM"]（V2 §十五）；
    trusted identity 字段禁止出现（extra='forbid' 层面的白名单由 program 层组装）。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    action_type: Literal["EXPENSE_CLAIM"]
    trip_id: str
    expense_items: list[ExpenseItemProposal]
    claimed_amount: Any  # 由 deterministic calculation 填入（int/float/Decimal 均可）
    reimbursable_amount: Any
    cost_center: str
    reason: str
    invoice_ids: list[str]
    stay_nights: int


class ExpenseItemProposal(BaseModel):
    """Proposal 内嵌费用项（等价 Java ExpenseActionProposal.ExpenseItemPayload）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: str
    amount: Any
    invoice_id: str
    description: str = ""


class ExpenseProposalContext(BaseModel):
    """expense_proposal_tool 的系统注入上下文（V2 §十三 / 追加约束 §1）。

    由 Tool Executor 从当前请求已成功的 tool_history / observation 确定性
    构造，作为 program-level runtime context 注入 —— 不会把 raw tool_history
    交给 LLM。
    """

    model_config = ConfigDict(extra="forbid")

    travel_record: list[dict[str, Any]] = []  # travel_record_tool success observation
    invoices: list[dict[str, Any]] = []       # invoice_verify_tool success observations
    policy_context: str = ""                  # rag_answer_tool success answer（仅知识解释用）
