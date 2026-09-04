"""Explicit Tool-to-WorkflowGuard assignments."""

from __future__ import annotations

from typing import Iterable

from app.agents.workflow_guard.contracts import WorkflowGuard


class WorkflowGuardRegistry:
    """Static guard registry; a workflow Tool has exactly one owner Guard."""

    def __init__(self, assignments: Iterable[tuple[str, WorkflowGuard]]):
        mapping: dict[str, WorkflowGuard] = {}
        for tool_name, guard in assignments:
            if tool_name in mapping:
                raise ValueError(
                    f'WorkflowGuardRegistry 不允许 Tool {tool_name} 同时归属多个 Guard'
                )
            mapping[tool_name] = guard
        self._guards = mapping

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._guards)

    def guard_for_tool(self, tool_name: str) -> WorkflowGuard | None:
        return self._guards.get(tool_name)
