"""Trusted context for one LangGraph invocation.

These values are supplied by the current Java -> Python request and are not
part of :class:`AgentState`.  They must never be recovered from an execution
snapshot as trusted input.
"""

from datetime import date
from typing import TypedDict


class AgentRuntimeContext(TypedDict):
    """Per-request trusted inputs used by Agent nodes and Tool Executor."""

    employee_id: str
    allow_eval: bool
    allow_business_actions: bool
    business_date: date | None
    trace_id: str
    deadline_monotonic: float
