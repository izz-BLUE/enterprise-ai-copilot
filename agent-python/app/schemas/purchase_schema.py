"""P4-3 Purchase Proposal 的 Python → Java 内部契约。"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PurchaseActionProposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    action_type: Literal['PURCHASE_REQUEST']
    item_name: str = Field(min_length=1, max_length=200)
    requested_budget: Decimal
    justification: str = Field(min_length=1, max_length=1000)
    available_budget: Decimal
    policy_result: Literal['PASS']
