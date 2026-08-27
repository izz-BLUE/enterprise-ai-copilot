"""Independent, durable Mock OA expense-submission boundary for P3-5B2a."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

WEBHOOK_EVENT_TYPE = "EXPENSE_APPROVAL_CHANGED"
TERMINAL_STATUSES = frozenset({"APPROVED", "REJECTED"})

Money = Annotated[Decimal, Field(strict=False, ge=Decimal(0), decimal_places=2)]


class ExpenseApprovalSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expenseId: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    employeeId: str = Field(min_length=1, max_length=64)
    tripId: str = Field(min_length=1, max_length=64)
    costCenter: str = Field(min_length=1, max_length=64)
    claimedAmount: Money
    reimbursableAmount: Money

    @model_validator(mode="after")
    def validate_reimbursable_amount(self) -> ExpenseApprovalSubmission:
        if self.reimbursableAmount > self.claimedAmount:
            raise ValueError("reimbursableAmount must not exceed claimedAmount")
        return self


class ExpenseApprovalResponse(BaseModel):
    requestId: str
    status: str


class MockOaStore:
    """SQLite gives idempotency a process-restart boundary without a workflow engine."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS expense_approval (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _canonical_payload(submission: ExpenseApprovalSubmission) -> str:
        payload = submission.model_dump()
        for field in ("claimedAmount", "reimbursableAmount"):
            payload[field] = "0" if payload[field] == 0 else format(payload[field].normalize(), "f")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def submit(self, key: str, submission: ExpenseApprovalSubmission) -> ExpenseApprovalResponse:
        payload = self._canonical_payload(submission)
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash, request_id, status FROM expense_approval WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row[0] != payload_hash:
                    raise ValueError("idempotency key was reused with another business payload")
                return ExpenseApprovalResponse(requestId=row[1], status=row[2])

            suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16].upper()
            request_id = f"OA-EXP-{suffix}"
            connection.execute(
                """
                INSERT INTO expense_approval(idempotency_key, payload_hash, request_id,
                    payload_json, status, created_at)
                VALUES (?, ?, ?, ?, 'PENDING', ?)
                """,
                (key, payload_hash, request_id, payload, datetime.now(UTC).isoformat()),
            )
            return ExpenseApprovalResponse(requestId=request_id, status="PENDING")

    def get(self, request_id: str) -> ExpenseApprovalResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_id, status FROM expense_approval WHERE request_id = ?", (request_id,)
            ).fetchone()
        return None if row is None else ExpenseApprovalResponse(requestId=row[0], status=row[1])

    def decide(self, request_id: str, decision: str) -> ExpenseApprovalResponse | None:
        if decision not in TERMINAL_STATUSES:
            raise ValueError("unsupported approval decision")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT request_id, status FROM expense_approval WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            current_status = row[1]
            if current_status == decision:
                return ExpenseApprovalResponse(requestId=row[0], status=current_status)
            if current_status != "PENDING":
                raise ValueError("opposite terminal approval decision is not allowed")
            connection.execute(
                """
                UPDATE expense_approval
                SET status = ?
                WHERE request_id = ? AND status = 'PENDING'
                """,
                (decision, request_id),
            )
            return ExpenseApprovalResponse(requestId=row[0], status=decision)


def _store() -> MockOaStore:
    return MockOaStore(os.getenv("MOCK_OA_DB_PATH", "/tmp/mock-oa.sqlite3"))


def _webhook_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("MOCK_OA_WEBHOOK_TIMEOUT_SECONDS", "5"))
    except ValueError:
        configured = 5.0
    return max(0.1, min(configured, 30.0))


def send_approval_webhook(request_id: str) -> bool:
    callback_url = os.getenv("MOCK_OA_WEBHOOK_URL", "").strip()
    secret = os.getenv("MOCK_OA_WEBHOOK_SECRET", "")
    if not callback_url or not secret:
        logger.warning("Mock OA webhook unavailable: callback URL or secret is not configured")
        return False

    timestamp = str(int(time.time()))
    body = json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": WEBHOOK_EVENT_TYPE,
            "requestId": request_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signing_input = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
    request = Request(
        callback_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Mock-OA-Timestamp": timestamp,
            "X-Mock-OA-Signature": f"v1={signature}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_webhook_timeout_seconds()) as response:
            response.read()
        logger.info("Mock OA webhook delivered requestId=%s", request_id)
        return True
    except (OSError, URLError, TimeoutError, ValueError) as exception:
        logger.warning(
            "Mock OA webhook delivery failed requestId=%s errorType=%s",
            request_id,
            type(exception).__name__,
        )
        return False


app = FastAPI(title="enterprise-ai-copilot Mock OA", version="P3-5B2a")
store = _store()


@app.post("/api/expense-approvals", response_model=ExpenseApprovalResponse, status_code=status.HTTP_200_OK)
def submit_expense_approval(
    submission: ExpenseApprovalSubmission,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExpenseApprovalResponse:
    if idempotency_key is None or not idempotency_key.startswith("expense:") or len(idempotency_key) > 80:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="valid Idempotency-Key is required")
    try:
        return store.submit(idempotency_key, submission)
    except ValueError as exception:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exception)) from exception


@app.get("/api/expense-approvals/{request_id}", response_model=ExpenseApprovalResponse)
def get_expense_approval(request_id: str) -> ExpenseApprovalResponse:
    record = store.get(request_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found")
    return record


def _decide_expense_approval(request_id: str, decision: str) -> ExpenseApprovalResponse:
    try:
        record = store.decide(request_id, decision)
    except ValueError as exception:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exception)) from exception
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found")

    # Mock OA commits the terminal state before this best-effort notification.
    send_approval_webhook(record.requestId)
    return record


@app.post(
    "/api/admin/expense-approvals/{request_id}/approve",
    response_model=ExpenseApprovalResponse,
    status_code=status.HTTP_200_OK,
)
def approve_expense_approval(request_id: str) -> ExpenseApprovalResponse:
    return _decide_expense_approval(request_id, "APPROVED")


@app.post(
    "/api/admin/expense-approvals/{request_id}/reject",
    response_model=ExpenseApprovalResponse,
    status_code=status.HTTP_200_OK,
)
def reject_expense_approval(request_id: str) -> ExpenseApprovalResponse:
    return _decide_expense_approval(request_id, "REJECTED")
