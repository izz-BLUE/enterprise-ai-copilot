"""严格的 checkpoint correlation 与 Java 权威 HITL 契约。"""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionType = Literal['ANNUAL_LEAVE_REQUEST', 'EXPENSE_CLAIM', 'PURCHASE_REQUEST']
HitlDecision = Literal['CONFIRMED', 'CANCELLED', 'EXPIRED', 'REJECTED']
HitlActionStatus = Literal['SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED']

_WAIT_DOMAIN = b'enterprise-ai-copilot:hitl-wait:v1\0'


class HitlWaitMarker(BaseModel):
    """LangGraph checkpoint state 中唯一持久化的 HITL 数据。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1]
    kind: Literal['BUSINESS_ACTION_CONFIRMATION']
    wait_id: str = Field(pattern=r'^wait_[0-9a-f]{64}$')
    execution_id: str = Field(pattern=r'^ex_[0-9a-f]{32}$')
    action_type: ActionType

    @classmethod
    def for_execution(cls, execution_id: str, action_type: ActionType) -> 'HitlWaitMarker':
        wait_id = 'wait_' + sha256(_WAIT_DOMAIN + execution_id.encode('utf-8')).hexdigest()
        return cls(
            schema_version=1,
            kind='BUSINESS_ACTION_CONFIRMATION',
            wait_id=wait_id,
            execution_id=execution_id,
            action_type=action_type,
        )


class HitlResumePayload(BaseModel):
    """用于恢复 graph 的 Java BusinessAction 权威结果。"""

    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal[1]
    wait_id: str = Field(pattern=r'^wait_[0-9a-f]{64}$')
    execution_id: str = Field(pattern=r'^ex_[0-9a-f]{32}$')
    decision: HitlDecision
    action_id: str | None = Field(default=None, max_length=64)
    action_type: ActionType
    action_status: HitlActionStatus
    request_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=255)

    @model_validator(mode='after')
    def validate_decision_status(self) -> 'HitlResumePayload':
        expected_status = {
            'CONFIRMED': 'SUCCEEDED',
            'CANCELLED': 'CANCELLED',
            'EXPIRED': 'EXPIRED',
            'REJECTED': 'FAILED',
        }[self.decision]
        if self.action_status != expected_status:
            raise ValueError('decision 与 action_status 不匹配')
        if self.decision == 'CONFIRMED' and self.action_id is None:
            raise ValueError('CONFIRMED 必须包含 action_id')
        return self


def proposal_action_type(value: dict | None) -> ActionType | None:
    """不信任额外字段，返回受支持的 action discriminator。"""
    if not isinstance(value, dict):
        return None
    action_type = value.get('action_type')
    if action_type in ('ANNUAL_LEAVE_REQUEST', 'EXPENSE_CLAIM', 'PURCHASE_REQUEST'):
        return action_type
    return None


__all__ = [
    'ActionType',
    'HitlActionStatus',
    'HitlDecision',
    'HitlResumePayload',
    'HitlWaitMarker',
    'proposal_action_type',
]
