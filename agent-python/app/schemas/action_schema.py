from datetime import date
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict


class AnnualLeaveActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_type: Literal["ANNUAL_LEAVE_REQUEST"]
    start_date: date
    end_date: date
    reason: str
    half_day: Literal["NONE", "AM", "PM"]


class AnnualLeaveClarification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    missing_fields: list[Literal["start_date", "end_date", "reason", "half_day"]]
    question: str
    # 仅供 Memory continuation 持久化；业务写入仍由 Java Proposal 链路负责。
    continuation_state: dict | None = None


class ProposalPlanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["proposal"] = "proposal"
    tool_name: Literal["plan_annual_leave_request"] = "plan_annual_leave_request"
    proposal: AnnualLeaveActionProposal


class ClarificationPlanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["clarification"] = "clarification"
    tool_name: Literal["plan_annual_leave_request"] = "plan_annual_leave_request"
    clarification: AnnualLeaveClarification


class InvalidPlanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["invalid"] = "invalid"
    error_code: Literal[
        "tool_call_invalid",
        "tool_call_missing",
        "tool_call_count_invalid",
        "tool_name_not_allowed",
        "tool_arguments_invalid",
        "provider_timeout",
        "provider_unavailable",
    ]


ToolPlanningResult: TypeAlias = (
    ProposalPlanningResult | ClarificationPlanningResult | InvalidPlanningResult
)
