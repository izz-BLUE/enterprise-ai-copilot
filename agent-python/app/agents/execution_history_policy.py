"""P3-2 execution history 的确定性归一化与合并策略。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from app.schemas.execution_history_schema import (
    CONTEXT_ONLY_REUSE_MODE,
    EXPENSE_REQUEST_TASK_TYPE,
    INVOICE_VERIFY_TOOL_NAME,
    MAX_EXECUTION_HISTORY_ENTRIES,
    MAX_HISTORY_ID_LENGTH,
    MAX_HISTORY_TEXT_LENGTH,
    MAX_TRAVEL_HISTORY_ITEMS,
    TRAVEL_RECORD_TOOL_NAME,
    ExecutionHistoryEntry,
    InvoiceExecutionArguments,
    InvoiceExecutionSummary,
    TravelExecutionArguments,
    TravelExecutionSummary,
    TravelExpenseDocumentSummary,
    TravelRecordSummary,
    validate_execution_history,
)


def _bounded_text(value: Any, *, limit: int = MAX_HISTORY_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _bounded_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_HISTORY_ID_LENGTH:
        return ""
    return normalized


def _observation_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    observation = item.get("observation")
    if isinstance(observation, str):
        try:
            observation = json.loads(observation)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(observation, dict) or observation.get("success") is not True:
        return None
    return observation


def _travel_entry(item: dict[str, Any]) -> ExecutionHistoryEntry | None:
    payload = _observation_payload(item)
    if payload is None:
        return None

    try:
        arguments = TravelExecutionArguments.model_validate(item.get("arguments") or {})
    except (TypeError, ValueError, ValidationError):
        return None

    trips: list[TravelRecordSummary] = []
    raw_items = payload.get("items")
    if isinstance(raw_items, list):
        for raw_trip in raw_items[:MAX_TRAVEL_HISTORY_ITEMS]:
            if not isinstance(raw_trip, dict):
                continue
            trip_id = _bounded_id(raw_trip.get("trip_id"))
            if not trip_id:
                continue
            documents: list[TravelExpenseDocumentSummary] = []
            raw_documents = raw_trip.get("expense_documents")
            if isinstance(raw_documents, list):
                for raw_document in raw_documents[:MAX_TRAVEL_HISTORY_ITEMS]:
                    if not isinstance(raw_document, dict):
                        continue
                    invoice_id = _bounded_id(raw_document.get("invoice_id"))
                    if not invoice_id:
                        continue
                    try:
                        documents.append(TravelExpenseDocumentSummary(
                            invoice_id=invoice_id,
                            category=_bounded_text(raw_document.get("category")),
                        ))
                    except ValidationError:
                        continue
            try:
                trips.append(TravelRecordSummary(
                    trip_id=trip_id,
                    status=_bounded_text(raw_trip.get("status")),
                    destination=_bounded_text(raw_trip.get("destination")),
                    start_date=_bounded_text(raw_trip.get("start_date")),
                    end_date=_bounded_text(raw_trip.get("end_date")),
                    expense_documents=documents,
                ))
            except ValidationError:
                continue

    return ExecutionHistoryEntry(
        task_type=EXPENSE_REQUEST_TASK_TYPE,
        tool_name=TRAVEL_RECORD_TOOL_NAME,
        arguments=arguments,
        summary=TravelExecutionSummary(trips=trips),
        reuse_mode=CONTEXT_ONLY_REUSE_MODE,
    )


def _invoice_entry(item: dict[str, Any]) -> ExecutionHistoryEntry | None:
    payload = _observation_payload(item)
    if payload is None:
        return None

    raw_arguments = item.get("arguments")
    requested_invoice_id = _bounded_id(
        raw_arguments.get("invoice_id") if isinstance(raw_arguments, dict) else None
    )
    if not requested_invoice_id or not isinstance(payload.get("valid"), bool) or not isinstance(
        payload.get("duplicate"), bool
    ):
        return None
    # arguments 定义了本次实际请求的 canonical invoice identity；响应只能回显它，
    # 不能借响应中的另一个 invoice_id 改写历史 key。
    if "invoice_id" in payload and payload.get("invoice_id") is not None:
        response_invoice_id = _bounded_id(payload.get("invoice_id"))
        if response_invoice_id != requested_invoice_id:
            return None
    invoice_id = requested_invoice_id

    amount = payload.get("amount")
    if amount is not None and (
        isinstance(amount, bool) or not isinstance(amount, (int, float))
        or isinstance(amount, float) and not math.isfinite(amount)
        or len(str(amount)) > 32
    ):
        amount = None

    try:
        arguments = InvoiceExecutionArguments(invoice_id=invoice_id)
        summary = InvoiceExecutionSummary(
            invoice_id=invoice_id,
            valid=payload["valid"],
            duplicate=payload["duplicate"],
            amount=amount,
            category=_bounded_text(payload.get("category")),
            issued_at=_bounded_text(payload.get("issued_at")),
            vendor=_bounded_text(payload.get("vendor")),
        )
        return ExecutionHistoryEntry(
            task_type=EXPENSE_REQUEST_TASK_TYPE,
            tool_name=INVOICE_VERIFY_TOOL_NAME,
            arguments=arguments,
            summary=summary,
            reuse_mode=CONTEXT_ONLY_REUSE_MODE,
        )
    except ValidationError:
        return None


@dataclass(frozen=True)
class _HistoryNormalizer:
    normalize: Callable[[dict[str, Any]], ExecutionHistoryEntry | None]


_NORMALIZERS: dict[str, _HistoryNormalizer] = {
    TRAVEL_RECORD_TOOL_NAME: _HistoryNormalizer(_travel_entry),
    INVOICE_VERIFY_TOOL_NAME: _HistoryNormalizer(_invoice_entry),
}


def normalize_successful_tool_history(tool_history: Any) -> list[ExecutionHistoryEntry]:
    """仅将当前请求中的 eligible success Tool 转为白名单摘要。"""

    if not isinstance(tool_history, list):
        return []
    entries: list[ExecutionHistoryEntry] = []
    for item in tool_history:
        if not isinstance(item, dict) or item.get("status") != "success":
            continue
        normalizer = _NORMALIZERS.get(item.get("tool_name"))
        if normalizer is None:
            continue
        entry = normalizer.normalize(item)
        if entry is not None:
            entries.append(entry)
    return entries


def execution_history_key(entry: ExecutionHistoryEntry) -> tuple[str, ...]:
    if entry.tool_name == TRAVEL_RECORD_TOOL_NAME:
        return (entry.task_type, entry.tool_name)
    return (
        entry.task_type,
        entry.tool_name,
        entry.summary.invoice_id,  # type: ignore[union-attr]
    )


def merge_execution_history(
    previous_history: Any,
    current_tool_history: Any,
) -> list[dict[str, Any]]:
    """校验、稳定去重并限制历史；相同 key 的新 entry 追加到末尾。"""

    merged: list[ExecutionHistoryEntry] = []
    entries = validate_execution_history(previous_history)
    entries.extend(normalize_successful_tool_history(current_tool_history))
    for entry in entries:
        key = execution_history_key(entry)
        merged = [existing for existing in merged if execution_history_key(existing) != key]
        merged.append(entry)
    merged = merged[-MAX_EXECUTION_HISTORY_ENTRIES:]
    return [entry.model_dump(mode="json") for entry in merged]
