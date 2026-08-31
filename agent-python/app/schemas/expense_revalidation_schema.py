"""确认时 Enterprise OA 事实传输的严格内部契约。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseRevalidationRequest(BaseModel):
    """Java 根据持久化 PendingAction 重建的标识。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1] = 1
    employee_id: str = Field(min_length=1, max_length=128)
    trip_id: str = Field(min_length=1, max_length=128)
    invoice_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator('employee_id', 'trip_id')
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('value must not be blank')
        return value

    @field_validator('invoice_ids')
    @classmethod
    def validate_invoice_ids(cls, value: list[str]) -> list[str]:
        normalized = [invoice_id.strip() for invoice_id in value]
        if any(not invoice_id for invoice_id in normalized):
            raise ValueError('invoice_ids must not contain blank values')
        if len(set(normalized)) != len(normalized):
            raise ValueError('invoice_ids must be unique')
        return normalized


class ExpenseRevalidationTripFact(BaseModel):
    """原始当前 trip 事实；Java 校验 status、owner 和日期。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    trip_id: str | None = None
    employee_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None


class ExpenseRevalidationInvoiceFact(BaseModel):
    """一条当前 invoice 结果，包含来源业务错误。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    invoice_id: str | None = None
    valid: bool | None = None
    duplicate: bool | None = None
    amount: Decimal | None = None
    category: str | None = None
    ownership_accepted: bool | None = None
    error_code: str | None = None


class ExpenseRevalidationResponse(BaseModel):
    """仅包含事实；此 adapter 永远不返回最终业务决定。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1] = 1
    success: bool
    trip: ExpenseRevalidationTripFact | None = None
    invoices: list[ExpenseRevalidationInvoiceFact] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
