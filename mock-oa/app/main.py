"""Independent, durable Mock OA expense-submission boundary for P3-5B1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field


class ExpenseApprovalSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expenseId: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    employeeId: str = Field(min_length=1, max_length=64)
    tripId: str = Field(min_length=1, max_length=64)
    costCenter: str = Field(min_length=1, max_length=64)
    claimedAmount: float = Field(ge=0)
    reimbursableAmount: float = Field(ge=0)


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
        return json.dumps(submission.model_dump(), sort_keys=True, separators=(",", ":"))

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


def _store() -> MockOaStore:
    return MockOaStore(os.getenv("MOCK_OA_DB_PATH", "/tmp/mock-oa.sqlite3"))


app = FastAPI(title="enterprise-ai-copilot Mock OA", version="P3-5B1")
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
