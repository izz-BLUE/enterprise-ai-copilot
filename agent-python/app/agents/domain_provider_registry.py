"""Workflow ownership and continuation registry for the Planner runtime.

The registry owns deterministic workflow lookup and completion handling. Semantic
tool routing is owned by the Planner plus the Capability Gate; this module does
not resolve a provider from the user's question.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.agents.tool_catalog import (
    TOOL_CATALOG,
)
from app.agents.workflow_guard.contracts import (
    _STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE,
    DomainContext,
    DomainToolCallRejected,
    WorkflowGuard,
    _latest_structured_tool_business_failure,
    _tool_invocation_has_business_success,
)
from app.agents.workflow_guard.expense_guard import ExpenseGuard
from app.agents.workflow_guard.expense_guard import (
    build_expense_proposal_context as _build_expense_proposal_context,
)
from app.agents.workflow_guard.leave_guard import LeaveGuard
from app.agents.workflow_guard.registry import WorkflowGuardRegistry
from app.schemas.planner_schema import (
    EXPENSE_PROPOSAL_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)


def build_expense_proposal_context(tool_history: Sequence[dict]) -> dict:
    """Compatibility export for the Executor's existing import path."""
    return _build_expense_proposal_context(tool_history)


class DomainProvider(Protocol):
    domain_key: str
    task_type: str
    proposal_tool_names: frozenset[str]
    workflow_tool_names: frozenset[str]
    guard: WorkflowGuard


class LeaveProvider:
    """Leave workflow owner; semantic routing is not performed here."""

    domain_key = 'leave'
    task_type = 'LEAVE_REQUEST'
    proposal_tool_names = frozenset({LEAVE_PROPOSAL_TOOL_NAME})
    workflow_tool_names = LeaveGuard.tool_names
    guard: LeaveGuard = LeaveGuard()

    def _active_continuation_state(self, memory_context: object) -> dict | None:
        return self.guard._active_continuation_state(memory_context)

    def continuation_state(self, context: DomainContext) -> dict | None:
        return self.guard.continuation_state(context)

    def continuation_prompt(self, question: str, state: dict) -> str:
        return self.guard.continuation_prompt(question, state)


class ExpenseProvider:
    """Expense workflow owner; semantic routing is not performed here."""

    domain_key = 'expense'
    task_type = 'EXPENSE_REQUEST'
    proposal_tool_names = frozenset({EXPENSE_PROPOSAL_TOOL_NAME})
    dependency_tools = ExpenseGuard.dependency_tools
    workflow_tool_names = ExpenseGuard.tool_names
    guard: ExpenseGuard = ExpenseGuard()

    def active_reason_task_state(self, memory_context: object) -> dict | None:
        return self.guard.active_reason_task_state(memory_context)

    def original_request(self, memory_context: object) -> str | None:
        return self.guard.original_request(memory_context)

    def continuation_prompt(self, question: str, original_request: str) -> str:
        return self.guard.continuation_prompt(question, original_request)


class DomainProviderRegistry:
    """静态 workflow owner registry; it never routes from question text."""

    def __init__(self, providers: Sequence[DomainProvider]):
        self._providers = tuple(providers)
        keys = [provider.domain_key for provider in self._providers]
        tasks = [provider.task_type for provider in self._providers]
        if len(set(keys)) != len(keys) or len(set(tasks)) != len(tasks):
            raise ValueError('DomainProviderRegistry 不允许重复 domain_key/task_type')

        self._tool_owners: dict[str, DomainProvider] = {}
        for provider in self._providers:
            for tool_name in getattr(provider, 'workflow_tool_names', frozenset()):
                owner = self._tool_owners.get(tool_name)
                if owner is not None and owner is not provider:
                    raise ValueError(f'Domain Tool 重复注册: {[tool_name]}')
                self._tool_owners[tool_name] = provider

        assignments = []
        for provider in self._providers:
            guard = getattr(provider, 'guard', None)
            for tool_name in getattr(provider, 'workflow_tool_names', frozenset()):
                if guard is not None:
                    assignments.append((tool_name, guard))
        self._workflow_guards = WorkflowGuardRegistry(assignments)

    @property
    def providers(self) -> tuple[DomainProvider, ...]:
        return self._providers

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Return the authoritative Planner catalog names."""
        return TOOL_CATALOG.tool_names

    @property
    def business_action_tools(self) -> frozenset[str]:
        """Return Catalog-declared proposal tools without question routing."""
        return frozenset(
            name for name in TOOL_CATALOG.tool_names
            if TOOL_CATALOG.prompt_spec(name).side_effect == 'PROPOSAL'
        )

    def workflow_guard_for_tool(self, tool_name: str) -> WorkflowGuard | None:
        return self._workflow_guards.guard_for_tool(tool_name)

    def workflow_guards_for_context(
        self, context: DomainContext
    ) -> tuple[WorkflowGuard, ...]:
        """Find Guards from observed workflow state, never from question intent."""
        names = {
            item.get('tool_name')
            for item in context.tool_history
            if isinstance(item, dict)
        }
        if context.continuation_original_request:
            names.add(EXPENSE_PROPOSAL_TOOL_NAME)
        if context.continuation_leave_state is not None:
            names.add(LEAVE_PROPOSAL_TOOL_NAME)

        guards: list[WorkflowGuard] = []
        for tool_name in names:
            guard = self._workflow_guards.guard_for_tool(tool_name)
            if (
                guard is not None
                and tool_name in getattr(guard, 'active_tool_names', frozenset())
                # A read-only travel/invoice lookup is not, by itself, an
                # active Expense claim. The semantic Planner owns that
                # distinction; a frozen reason, continuation, or Proposal
                # observation is the deterministic workflow evidence that
                # enables the Expense Guard restrictions.
                and (
                    guard.domain_key != 'expense'
                    or context.continuation_original_request
                    or context.request_expense_reason is not None
                    or EXPENSE_PROPOSAL_TOOL_NAME in names
                )
                and guard not in guards
            ):
                guards.append(guard)
        return tuple(guards)

    def validate_selected_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: DomainContext,
    ) -> None:
        """Validate one selected Tool against its Guard and active workflow."""
        selected_guard = self.workflow_guard_for_tool(tool_name)
        active_guards = self.workflow_guards_for_context(context)
        if (
            selected_guard is not None
            and active_guards
            and selected_guard not in active_guards
        ):
            raise DomainToolCallRejected(
                'domain_tool_mismatch',
                '当前请求领域与目标 Tool 所属领域不一致，已拒绝执行。',
            )
        if selected_guard is not None:
            selected_guard.validate_tool_call(tool_name, arguments, context)

    def terminal_clarification_for_workflow(
        self, context: DomainContext
    ) -> str | None:
        """Read clarification from an already active workflow Guard."""
        for guard in self.workflow_guards_for_context(context):
            message = guard.terminal_clarification(context)
            if message is not None:
                return message
        return None

    def validate_completion_for_workflow(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
    ) -> None:
        """Apply completion checks using observed Tools and active Guards."""
        if (
            decision.action == 'finish'
            and decision.reason_code == 'task_complete'
            and _latest_structured_tool_business_failure(context.tool_history) is not None
        ):
            raise PlannerDecisionError(_STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE)
        for guard in self.workflow_guards_for_context(context):
            validator = getattr(guard, 'validate_completion_for_workflow', None)
            if validator is not None:
                validator(decision, tools, context)

    def recover_completion_for_workflow(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None:
        """Recover from a validation error in the active workflow only."""
        for guard in self.workflow_guards_for_context(context):
            recover = getattr(guard, 'recover_completion_for_workflow', None)
            if recover is None:
                continue
            recovered = recover(decision, tools, context, error_code)
            if recovered is not None:
                return recovered
        return None

    def postprocess_selected_tool(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
    ) -> tuple[PlannerDecision, dict[str, object]]:
        """Run selected/active Guard state handling without semantic rerouting."""
        guards: list[WorkflowGuard] = []
        selected_guard = (
            self.workflow_guard_for_tool(decision.tool_name)
            if decision.action == 'tool' and decision.tool_name
            else None
        )
        if selected_guard is not None:
            guards.append(selected_guard)
        for guard in self.workflow_guards_for_context(context):
            if guard not in guards:
                guards.append(guard)

        updates: dict[str, object] = {}
        for guard in guards:
            postprocess = getattr(guard, 'postprocess_selected_tool', None)
            if postprocess is None:
                continue
            decision, guard_updates = postprocess(decision, tools, context)
            updates.update(guard_updates)
        return decision, updates

    def provider_for_tool(self, tool_name: str) -> DomainProvider | None:
        return self._tool_owners.get(tool_name)

    def completion_contract(self, tools: Sequence[str]) -> str:
        """Aggregate completion contracts from Guards owning visible tools."""
        guards: list[WorkflowGuard] = []
        visible = set(tools)
        for provider in self._providers:
            guard = getattr(provider, 'guard', None)
            if guard is not None and visible & set(getattr(guard, 'tool_names', ())):
                if guard not in guards:
                    guards.append(guard)
        contracts = [
            guard.completion_contract(tools)
            for guard in guards
        ]
        return '\n\n'.join(contract for contract in contracts if contract)

    def is_completed_success(self, item: dict) -> bool:
        if not _tool_invocation_has_business_success(item):
            return False
        guard = self.workflow_guard_for_tool(item.get('tool_name'))
        if guard is None:
            return item.get('status') == 'success'
        return guard.is_completed_success(item)

    def validation_metadata(self, message: str) -> tuple[str, str, str] | None:
        known = {
            'finish 前未完成 leave_proposal_tool Proposal 阶段': (
                'planner_completion_validation', 'leave_proposal_missing',
                '当前用户目标包含年假申请，但尚未完成 leave_proposal_tool；请继续规划剩余目标。',
            ),
            'finish 前未完成 leave_balance_tool 当前余额查询': (
                'planner_completion_validation', 'leave_balance_missing',
                '当前本人年假余额尚未通过 leave_balance_tool 查询。',
            ),
            'finish 前未完成 expense_proposal_tool Proposal 阶段': (
                'planner_completion_validation', 'expense_proposal_missing',
                '当前报销申请尚未达到合法完成状态；不得 finish；下一步必须输出 '
                'action="tool"，并从当前合法能力清单选择尚未成功的 prerequisite Tool。',
            ),
            _STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE: (
                'planner_completion_validation', 'structured_tool_business_failure',
                _STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE,
            ),
        }
        return known.get(message)

    def continuation_original_request(self, memory_context: object) -> str | None:
        provider = next(
            (item for item in self._providers if item.task_type == 'EXPENSE_REQUEST'),
            None,
        )
        return provider.original_request(memory_context) if provider is not None else None

    def continuation_waiting(self, memory_context: object) -> bool:
        provider = next(
            (item for item in self._providers if item.task_type == 'EXPENSE_REQUEST'),
            None,
        )
        return bool(provider and provider.active_reason_task_state(memory_context))

    def continuation_prompt(self, question: str, original_request: str) -> str:
        provider = next(
            (item for item in self._providers if item.task_type == 'EXPENSE_REQUEST'),
            None,
        )
        return provider.continuation_prompt(question, original_request) if provider else ''

    def leave_continuation_state(
        self, question: str, memory_context: object
    ) -> dict | None:
        provider = next(
            (item for item in self._providers if item.task_type == 'LEAVE_REQUEST'),
            None,
        )
        if provider is None:
            return None
        context = DomainContext(question=question or '', memory_context=memory_context)
        return provider.continuation_state(context)

    def leave_continuation_prompt(self, question: str, state: dict) -> str:
        provider = next(
            (item for item in self._providers if item.task_type == 'LEAVE_REQUEST'),
            None,
        )
        return provider.continuation_prompt(question, state) if provider else ''


# P4-1 intentionally uses a small, explicit static registry. Adding a future
# domain requires a deliberate registration and schema/tool review.
DOMAIN_PROVIDER_REGISTRY = DomainProviderRegistry(
    (ExpenseProvider(), LeaveProvider())
)
