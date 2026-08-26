"""P3-2 cross-request execution history 的严格内部 Schema。

Execution history 是同一业务任务的有限历史摘要，不是当前业务事实。所有字段均由
程序层白名单构造；Tool observation 不会直接进入这些模型。
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_EXECUTION_HISTORY_ENTRIES = 16
MAX_TRAVEL_HISTORY_ITEMS = 10
MAX_HISTORY_TEXT_LENGTH = 256
MAX_HISTORY_ID_LENGTH = 128

EXPENSE_REQUEST_TASK_TYPE = "EXPENSE_REQUEST"
CONTEXT_ONLY_REUSE_MODE = "CONTEXT_ONLY"
TRAVEL_RECORD_TOOL_NAME = "travel_record_tool"
INVOICE_VERIFY_TOOL_NAME = "invoice_verify_tool"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TravelExecutionArguments(_StrictModel):
    """travel_record_tool 的 LLM 参数快照；该 Tool 没有业务参数。"""


class InvoiceExecutionArguments(_StrictModel):
    """invoice_verify_tool 的 LLM 参数快照。"""

    invoice_id: str = Field(min_length=1, max_length=MAX_HISTORY_ID_LENGTH)


class TravelExpenseDocumentSummary(_StrictModel):
    invoice_id: str = Field(min_length=1, max_length=MAX_HISTORY_ID_LENGTH)
    category: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)


class TravelRecordSummary(_StrictModel):
    trip_id: str = Field(min_length=1, max_length=MAX_HISTORY_ID_LENGTH)
    status: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    destination: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    start_date: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    end_date: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    expense_documents: list[TravelExpenseDocumentSummary] = Field(
        default_factory=list,
        max_length=MAX_TRAVEL_HISTORY_ITEMS,
    )


class TravelExecutionSummary(_StrictModel):
    trips: list[TravelRecordSummary] = Field(
        default_factory=list,
        max_length=MAX_TRAVEL_HISTORY_ITEMS,
    )


class InvoiceExecutionSummary(_StrictModel):
    invoice_id: str = Field(min_length=1, max_length=MAX_HISTORY_ID_LENGTH)
    valid: bool
    duplicate: bool
    amount: int | float | None = None
    category: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    issued_at: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)
    vendor: str = Field(default="", max_length=MAX_HISTORY_TEXT_LENGTH)

    @field_validator("amount")
    @classmethod
    def validate_bounded_amount(cls, value: int | float | None) -> int | float | None:
        if value is None:
            return None
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("amount 必须是有限数字")
        if len(str(value)) > 32:
            raise ValueError("amount 超出历史摘要长度限制")
        return value


class ExecutionHistoryEntry(_StrictModel):
    """一个可跨请求读取的、仅 CONTEXT_ONLY 的业务执行摘要。"""

    schema_version: Literal[1] = 1
    task_type: Literal["EXPENSE_REQUEST"]
    tool_name: Literal["travel_record_tool", "invoice_verify_tool"]
    arguments: TravelExecutionArguments | InvoiceExecutionArguments
    summary: TravelExecutionSummary | InvoiceExecutionSummary
    reuse_mode: Literal["CONTEXT_ONLY"] = CONTEXT_ONLY_REUSE_MODE

    @model_validator(mode="after")
    def validate_tool_payload_pair(self) -> "ExecutionHistoryEntry":
        if self.tool_name == TRAVEL_RECORD_TOOL_NAME:
            if not isinstance(self.arguments, TravelExecutionArguments):
                raise ValueError("travel_record_tool 的 arguments 结构无效")
            if not isinstance(self.summary, TravelExecutionSummary):
                raise ValueError("travel_record_tool 的 summary 结构无效")
        elif self.tool_name == INVOICE_VERIFY_TOOL_NAME:
            if not isinstance(self.arguments, InvoiceExecutionArguments):
                raise ValueError("invoice_verify_tool 的 arguments 结构无效")
            if not isinstance(self.summary, InvoiceExecutionSummary):
                raise ValueError("invoice_verify_tool 的 summary 结构无效")
        return self


def validate_execution_history(entries: Any) -> list[ExecutionHistoryEntry]:
    """严格校验并限制历史条目；非法条目被丢弃，不把其内容带入 Planner。"""

    if not isinstance(entries, list):
        return []
    validated: list[ExecutionHistoryEntry] = []
    for entry in entries:
        try:
            validated.append(ExecutionHistoryEntry.model_validate(entry))
        except (TypeError, ValueError):
            continue
    return validated[-MAX_EXECUTION_HISTORY_ENTRIES:]
