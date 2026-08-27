"""Strict marker persisted with an unfinished Planner-first execution."""

from datetime import date
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FINGERPRINT_PREFIX = b'enterprise-ai-copilot:execution-request:v1\0'
_ACTOR_SCOPE_FINGERPRINT_PREFIX = b'enterprise-ai-copilot:execution-actor:v1\0'
_DATE_PATTERN = r'^\d{4}-\d{2}-\d{2}$'


class ExecutionRecoveryMarker(BaseModel):
    """Deterministic control metadata for one resumable graph execution.

    This marker is not an identity, authorization, credential, nonce, or
    business fact. Trusted request values remain in Runtime Context and are
    deliberately absent from this schema.
    """

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1]
    execution_id: str = Field(pattern=r'^ex_[0-9a-f]{32}$')
    request_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    actor_scope_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    execution_date_anchor: str | None = Field(default=None, pattern=_DATE_PATTERN)
    graph_variant: Literal['planner-v1']

    @field_validator('execution_date_anchor')
    @classmethod
    def _validate_date_anchor(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value


def fingerprint_request(question: str) -> str:
    """Hash the exact Python-received question without normalization."""
    return sha256(_FINGERPRINT_PREFIX + question.encode('utf-8')).hexdigest()


def fingerprint_actor_scope(employee_id: str) -> str:
    """Bind recovery to the current trusted employee scope without persisting it."""
    return sha256(
        _ACTOR_SCOPE_FINGERPRINT_PREFIX + employee_id.encode('utf-8')
    ).hexdigest()


def new_execution_recovery_marker(
    question: str,
    business_date: date | None,
    employee_id: str,
) -> dict:
    """Create a fresh strict marker for a Planner-first execution."""
    marker = ExecutionRecoveryMarker(
        schema_version=1,
        execution_id=f'ex_{uuid4().hex}',
        request_fingerprint=fingerprint_request(question),
        actor_scope_fingerprint=fingerprint_actor_scope(employee_id),
        execution_date_anchor=business_date.isoformat() if business_date else None,
        graph_variant='planner-v1',
    )
    return marker.model_dump()
