"""Planner 领域 Provider 兼容 facade 与显式路由注册表。

Provider 保留旧的 matches/resolve 兼容行为，并把确定性业务流程委托给
workflow_guard；Planner semantic metadata 则统一来自 tool_catalog。
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.agents.tool_catalog import (
    _PLATFORM_PROMPT_SPECS,
    TOOL_CATALOG,
    ToolPromptSpec,
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
from app.services import expense_input_service
from app.services.annual_leave_input_service import (
    is_annual_leave_action_intent,
    is_personal_annual_leave_balance_query,
)


def build_expense_proposal_context(tool_history: Sequence[dict]) -> dict:
    """Compatibility export for the Executor's existing import path."""
    return _build_expense_proposal_context(tool_history)


class DomainProviderAmbiguityError(PlannerDecisionError):
    """一个请求同时命中多个 Provider。"""


class DomainProvider(Protocol):
    domain_key: str
    task_type: str
    proposal_tool_names: frozenset[str]
    semantic_slots: frozenset[str]
    capability_tools: frozenset[str]

    def matches(self, context: DomainContext) -> bool: ...

    def is_business_action_intent(self, context: DomainContext) -> bool: ...

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]: ...

    def terminal_clarification(self, context: DomainContext) -> str | None: ...

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None: ...

    def completion_contract(self, tools: Sequence[str]) -> str: ...

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None: ...

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None: ...

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]: ...

    def prompt_specs(self) -> dict[str, ToolPromptSpec]: ...

    def is_completed_success(self, item: dict) -> bool: ...


class LeaveProvider:
    """Leave 自然语言路由兼容层；业务流程交给 LeaveGuard。"""

    domain_key = 'leave'
    task_type = 'LEAVE_REQUEST'
    proposal_tool_names = frozenset({LEAVE_PROPOSAL_TOOL_NAME})
    semantic_slots = frozenset()
    capability_tools = frozenset({LEAVE_PROPOSAL_TOOL_NAME})
    workflow_tool_names = LeaveGuard.tool_names
    guard: LeaveGuard = LeaveGuard()

    def _active_continuation_state(self, memory_context: object) -> dict | None:
        return self.guard._active_continuation_state(memory_context)

    def continuation_state(self, context: DomainContext) -> dict | None:
        return self.guard.continuation_state(context)

    def matches(self, context: DomainContext) -> bool:
        return (
            is_annual_leave_action_intent(context.question)
            or is_personal_annual_leave_balance_query(context.question)
            or self.continuation_state(context) is not None
        )

    def is_business_action_intent(self, context: DomainContext) -> bool:
        return (
            (
                is_annual_leave_action_intent(context.question)
                and not is_personal_annual_leave_balance_query(context.question)
            )
            or self.continuation_state(context) is not None
        )

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        return self.guard.legal_tools(
            tools,
            context,
            balance_query=is_personal_annual_leave_balance_query(context.question),
        )

    def terminal_clarification(self, context: DomainContext) -> str | None:
        return self.guard.terminal_clarification(context)

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        return self.guard.validate_tool_call(tool_name, arguments, context)

    def completion_contract(self, tools: Sequence[str]) -> str:
        return self.guard.completion_contract(tools)

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        return self.guard.validate_completion(
            decision,
            tools,
            context,
            balance_query=is_personal_annual_leave_balance_query(context.question),
        )

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None:
        return self.guard.recover_completion_decision(
            decision,
            tools,
            context,
            error_code,
            balance_query=is_personal_annual_leave_balance_query(context.question),
        )

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        return self.guard.postprocess_decision(decision, tools, context)

    def continuation_prompt(self, question: str, state: dict) -> str:
        return self.guard.continuation_prompt(question, state)

    def prompt_specs(self) -> dict[str, ToolPromptSpec]:
        return TOOL_CATALOG.specs_for_domain(self.domain_key)

    def is_completed_success(self, item: dict) -> bool:
        return self.guard.is_completed_success(item)


class ExpenseProvider:
    """Expense 自然语言路由兼容层；业务流程交给 ExpenseGuard。"""

    domain_key = 'expense'
    task_type = 'EXPENSE_REQUEST'
    proposal_tool_names = frozenset({EXPENSE_PROPOSAL_TOOL_NAME})
    semantic_slots = frozenset({'expense_reason'})
    capability_tools = frozenset({EXPENSE_PROPOSAL_TOOL_NAME})
    dependency_tools = ExpenseGuard.dependency_tools
    workflow_tool_names = ExpenseGuard.tool_names
    guard: ExpenseGuard = ExpenseGuard()

    def matches(self, context: DomainContext) -> bool:
        return bool(
            expense_input_service.is_expense_claim_intent(context.question)
            or context.continuation_original_request
        )

    def is_business_action_intent(self, context: DomainContext) -> bool:
        if not self.matches(context):
            return False
        # 显式 trip / invoice 引用仍可用于读取报销事实；只有需要进入
        # Proposal capability 的直接申请才走未授权业务动作 preflight。
        return not (
            expense_input_service.extract_trip_reference(context.question)
            or expense_input_service.extract_invoice_references(context.question)
        )

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        return self.guard.legal_tools(
            tools, context, matched=self.matches(context)
        )

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None:
        return self.guard.recover_completion_decision(
            decision, tools, context, error_code
        )

    def _selected_invoice_progress(self, *args: Any, **kwargs: Any) -> Any:
        return self.guard._selected_invoice_progress(*args, **kwargs)

    @staticmethod
    def _tool_decision(
        tool_name: str,
        arguments: dict[str, Any],
        reason_code: str,
        expense_reason: str | None,
    ) -> PlannerDecision:
        return ExpenseGuard._tool_decision(
            tool_name, arguments, reason_code, expense_reason
        )

    def terminal_clarification(self, context: DomainContext) -> str | None:
        return self.guard.terminal_clarification(context)

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        return self.guard.validate_tool_call(
            tool_name, arguments, context, matched=self.matches(context)
        )

    def invoice_scope_allowed(
        self, question: str, tool_history: Sequence[dict], invoice_id: str | None
    ) -> bool:
        return self.guard.invoice_scope_allowed(question, tool_history, invoice_id)

    def completion_contract(self, tools: Sequence[str]) -> str:
        return self.guard.completion_contract(tools)

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        return self.guard.validate_completion(decision, tools, context)

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        return self.guard.postprocess_decision(
            decision,
            tools,
            context,
            matched=self.matches(context),
            claim_intent=expense_input_service.is_expense_claim_intent(context.question),
        )

    @staticmethod
    def _normalize_reason(value: object) -> str | None:
        return ExpenseGuard._normalize_reason(value)

    def is_completed_success(self, item: dict) -> bool:
        return self.guard.is_completed_success(item)

    def prompt_specs(self) -> dict[str, ToolPromptSpec]:
        return TOOL_CATALOG.specs_for_domain(self.domain_key)

    def active_reason_task_state(self, memory_context: object) -> dict | None:
        return self.guard.active_reason_task_state(memory_context)

    def original_request(self, memory_context: object) -> str | None:
        return self.guard.original_request(memory_context)

    def continuation_prompt(self, question: str, original_request: str) -> str:
        return self.guard.continuation_prompt(question, original_request)


class DomainProviderRegistry:
    """显式静态 Provider 注册表；不做动态 discovery。"""

    def __init__(self, providers: Sequence[DomainProvider]):
        self._providers = tuple(providers)
        keys = [provider.domain_key for provider in self._providers]
        tasks = [provider.task_type for provider in self._providers]
        if len(set(keys)) != len(keys) or len(set(tasks)) != len(tasks):
            raise ValueError('DomainProviderRegistry 不允许重复 domain_key/task_type')

        self._prompt_specs = dict(_PLATFORM_PROMPT_SPECS)
        for provider in self._providers:
            provider_specs = provider.prompt_specs()
            overlap = self._prompt_specs.keys() & provider_specs.keys()
            if overlap:
                raise ValueError(f'Domain Provider Tool prompt 重复注册: {sorted(overlap)}')
            self._prompt_specs.update(provider_specs)
        ordered_specs = {
            name: self._prompt_specs[name]
            for name in TOOL_CATALOG.tool_names
            if name in self._prompt_specs
        }
        ordered_specs.update({
            name: spec
            for name, spec in self._prompt_specs.items()
            if name not in ordered_specs
        })
        self._prompt_specs = ordered_specs

        self._tool_owners: dict[str, DomainProvider] = {}
        for provider in self._providers:
            owned_names = (
                set(provider.prompt_specs())
                | set(provider.proposal_tool_names)
                | set(provider.capability_tools)
                | set(getattr(provider, 'dependency_tools', frozenset()))
            )
            for tool_name in owned_names:
                owner = self._tool_owners.get(tool_name)
                if owner is not None and owner is not provider:
                    raise ValueError(f'Domain Provider Tool prompt 重复注册: {[tool_name]}')
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
        return tuple(self._prompt_specs)

    @property
    def business_action_tools(self) -> frozenset[str]:
        """所有领域声明的业务 capability Tool；不做动态 discovery。"""
        return frozenset(
            name for provider in self._providers
            for name in provider.capability_tools
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

    def capability_tools_for_question(self, question: str) -> list[str]:
        """按当前请求贡献领域 capability，不改变上游权限集合。"""
        context = DomainContext(question=question or '')
        return [
            name for provider in self._providers if provider.matches(context)
            for name in provider.prompt_specs()
            if name in provider.capability_tools
        ]

    def resolve(self, context: DomainContext) -> DomainProvider | None:
        matches = tuple(provider for provider in self._providers if provider.matches(context))
        if len(matches) > 1:
            raise DomainProviderAmbiguityError(
                f'请求同时命中多个业务领域: {", ".join(provider.domain_key for provider in matches)}'
            )
        return matches[0] if matches else None

    def provider_for_tool(self, tool_name: str) -> DomainProvider | None:
        return self._tool_owners.get(tool_name)

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        provider = self.resolve(context)
        if provider is None:
            return list(tools)
        original = list(tools)
        legal = provider.legal_tools(original, context)
        allowed = set(original)
        return [
            name for name in legal
            if name in allowed
            and (
                (owner := self.provider_for_tool(name)) is None
                or owner is provider
            )
        ]

    def terminal_clarification(self, context: DomainContext) -> str | None:
        provider = self.resolve(context)
        return provider.terminal_clarification(context) if provider is not None else None

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        resolved_provider = self.resolve(context)
        tool_owner = self.provider_for_tool(tool_name)
        if (
            resolved_provider is not None
            and tool_owner is not None
            and resolved_provider is not tool_owner
        ):
            raise DomainToolCallRejected(
                'domain_tool_mismatch',
                '当前请求领域与目标 Tool 所属领域不一致，已拒绝执行。',
            )
        if tool_owner is not None:
            tool_owner.validate_tool_call(tool_name, arguments, context)

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        if (
            decision.action == 'finish'
            and decision.reason_code == 'task_complete'
            and _latest_structured_tool_business_failure(context.tool_history) is not None
        ):
            raise PlannerDecisionError(_STRUCTURED_TOOL_FAILURE_COMPLETION_MESSAGE)
        provider = self.resolve(context)
        if provider is not None:
            provider.validate_completion(decision, tools, context)

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
    ) -> PlannerDecision | None:
        provider = self.resolve(context)
        if provider is None:
            return None
        return provider.recover_completion_decision(
            decision, tools, context, error_code
        )

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        resolved_provider = self.resolve(context)
        tool_owner_provider = None
        if decision.action == 'tool' and decision.tool_name:
            tool_owner_provider = self.provider_for_tool(decision.tool_name)

        providers = []
        if tool_owner_provider is not None:
            providers.append(tool_owner_provider)
        if resolved_provider is not None and resolved_provider is not tool_owner_provider:
            providers.append(resolved_provider)
        if not providers:
            return decision, {}

        updates: dict[str, object] = {}
        for provider in providers:
            decision, provider_updates = provider.postprocess_decision(
                decision, tools, context
            )
            updates.update(provider_updates)
        return decision, updates

    def completion_contract(self, tools: Sequence[str]) -> str:
        contracts = [
            provider.completion_contract(tools)
            for provider in self._providers
            if set(provider.prompt_specs()) & set(tools)
        ]
        return '\n\n'.join(contract for contract in contracts if contract)

    def prompt_spec(self, tool_name: str) -> ToolPromptSpec:
        return self._prompt_specs[tool_name]

    def is_completed_success(self, item: dict) -> bool:
        if not _tool_invocation_has_business_success(item):
            return False
        provider = self.provider_for_tool(item.get('tool_name'))
        if provider is None:
            return item.get('status') == 'success'
        return provider.is_completed_success(item)

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
