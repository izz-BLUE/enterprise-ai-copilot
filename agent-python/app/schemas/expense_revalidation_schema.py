"""Strict internal contract for confirm-time Enterprise OA fact transport."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseRevalidationRequest(BaseModel):
    """Identifiers reconstructed by Java from its persisted PendingAction."""

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
    """Raw current trip facts; Java validates status, owner and dates."""

    model_config = ConfigDict(extra='forbid', strict=True)

    trip_id: str | None = None
    employee_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None


class ExpenseRevalidationInvoiceFact(BaseModel):
    """One current invoice result, including source business errors."""

    model_config = ConfigDict(extra='forbid', strict=True)

    invoice_id: str | None = None
    valid: bool | None = None
    duplicate: bool | None = None
    amount: Decimal | None = None
    category: str | None = None
    ownership_accepted: bool | None = None
    error_code: str | None = None


class ExpenseRevalidationResponse(BaseModel):
    """Facts only; this adapter never returns a final business decision."""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1] = 1
    success: bool
    trip: ExpenseRevalidationTripFact | None = None
    invoices: list[ExpenseRevalidationInvoiceFact] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
