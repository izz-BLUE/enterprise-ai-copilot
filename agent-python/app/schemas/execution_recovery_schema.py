"""与未完成 Planner-first execution 一起持久化的严格 marker。"""

from datetime import date
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FINGERPRINT_PREFIX = b'enterprise-ai-copilot:execution-request:v1\0'
_ACTOR_SCOPE_FINGERPRINT_PREFIX = b'enterprise-ai-copilot:execution-actor:v1\0'
_DATE_PATTERN = r'^\d{4}-\d{2}-\d{2}$'


class ExecutionRecoveryMarker(BaseModel):
    """一个可恢复 graph execution 的确定性控制元数据。

    该 marker 不是身份、授权、凭据、nonce 或业务事实。可信请求值保留在 Runtime
    Context 中，并有意不出现在此 schema 中。
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
    """对 Python 收到的原始问题进行哈希，不做规范化。"""
    return sha256(_FINGERPRINT_PREFIX + question.encode('utf-8')).hexdigest()


def fingerprint_actor_scope(employee_id: str) -> str:
    """将恢复绑定到当前可信员工作用域，但不持久化该作用域。"""
    return sha256(
        _ACTOR_SCOPE_FINGERPRINT_PREFIX + employee_id.encode('utf-8')
    ).hexdigest()


def new_execution_recovery_marker(
    question: str,
    business_date: date | None,
    employee_id: str,
) -> dict:
    """为 Planner-first execution 创建新的严格 marker。"""
    marker = ExecutionRecoveryMarker(
        schema_version=1,
        execution_id=f'ex_{uuid4().hex}',
        request_fingerprint=fingerprint_request(question),
        actor_scope_fingerprint=fingerprint_actor_scope(employee_id),
        execution_date_anchor=business_date.isoformat() if business_date else None,
        graph_variant='planner-v1',
    )
    return marker.model_dump()
