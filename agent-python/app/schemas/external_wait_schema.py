"""一个外部报销审批 wait 的严格持久化 correlation。"""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EXTERNAL_WAIT_DOMAIN = b'enterprise-ai-copilot:external-wait:v1\0'


class ExternalWaitMarker(BaseModel):
    """单个 P3-5 报销审批 wait 的 checkpoint-safe marker。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1]
    kind: Literal['EXPENSE_APPROVAL']
    wait_id: str = Field(pattern=r'^extwait_[0-9a-f]{64}$')
    execution_id: str = Field(pattern=r'^ex_[0-9a-f]{32}$')
    action_type: Literal['EXPENSE_CLAIM']
    request_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$',
    )

    @classmethod
    def for_execution(cls, execution_id: str, request_id: str) -> 'ExternalWaitMarker':
        wait_id = 'extwait_' + sha256(
            _EXTERNAL_WAIT_DOMAIN
            + execution_id.encode('utf-8')
            + b'\0'
            + request_id.encode('utf-8')
        ).hexdigest()
        return cls(
            schema_version=1,
            kind='EXPENSE_APPROVAL',
            wait_id=wait_id,
            execution_id=execution_id,
            action_type='EXPENSE_CLAIM',
            request_id=request_id,
        )


class ExternalResumePayload(BaseModel):
    """用于恢复 graph 的 Java 权威 OA 终态决定。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1]
    wait_id: str = Field(pattern=r'^extwait_[0-9a-f]{64}$')
    execution_id: str = Field(pattern=r'^ex_[0-9a-f]{32}$')
    action_type: Literal['EXPENSE_CLAIM']
    request_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$',
    )
    decision: Literal['APPROVED', 'REJECTED']
    status: Literal['APPROVED', 'REJECTED']
    message: str = Field(min_length=1, max_length=255)

    @field_validator('message')
    @classmethod
    def validate_safe_message(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('message 必须是无控制字符的 bounded safe string')
        return value

    @model_validator(mode='after')
    def validate_terminal_status(self) -> 'ExternalResumePayload':
        if self.status != self.decision:
            raise ValueError('decision 与 status 不匹配')
        return self


__all__ = ['ExternalResumePayload', 'ExternalWaitMarker']
