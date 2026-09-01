"""Planner 领域 Provider 合约与显式注册表。

平台层负责 capability-visible 工具集合和 ToolSpec 执行机制；Provider 只负责
领域决策规则。注册表是静态构造的，Provider 不能从传入集合之外增加工具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Sequence

from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_MAX_LIMIT,
    LEAVE_REQUEST_MIN_LIMIT,
    LEAVE_REQUEST_TOOL_NAME,
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
    PURCHASE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)
from app.services import expense_input_service
from app.services.annual_leave_input_service import is_annual_leave_action_intent


@dataclass(frozen=True)
class DomainContext:
    """Provider 可消费的当前请求视图，不包含 trusted Runtime Context。"""

    question: str
    tool_history: tuple[dict, ...] = field(default_factory=tuple)
    request_expense_reason: str | None = None
    action_proposal: object = None
    continuation_original_request: str | None = None
    memory_context: object = None
    step_count: int = 0
    purchase_item: str | None = None
    purchase_budget: Decimal | None = None
    purchase_justification: str | None = None

    @classmethod
    def from_state(cls, state: dict) -> "DomainContext":
        history = state.get('tool_history', [])
        planner_decision = state.get('planner_decision')
        planner_decision = planner_decision if isinstance(planner_decision, dict) else {}
        return cls(
            question=state.get('question', ''),
            tool_history=tuple(history) if isinstance(history, list) else tuple(),
            request_expense_reason=state.get('request_expense_reason'),
            action_proposal=state.get('action_proposal'),
            continuation_original_request=state.get('continuation_original_request'),
            memory_context=state.get('memory_context'),
            step_count=state.get('step_count', 0),
            purchase_item=state.get('purchase_item', planner_decision.get('purchase_item')),
            purchase_budget=state.get('purchase_budget', planner_decision.get('purchase_budget')),
            purchase_justification=state.get(
                'purchase_justification', planner_decision.get('purchase_justification')
            ),
        )


@dataclass(frozen=True)
class ToolPromptSpec:
    description: str
    argument_contract: str
    reason_code: str
    example: dict[str, Any]
    usage_rule: str = ''
    freshness_rule: str = ''


class DomainToolCallRejected(PlannerDecisionError):
    """领域 second gate 拒绝一次非法 Tool 调用。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class DomainProviderAmbiguityError(PlannerDecisionError):
    """一个请求同时命中多个 Provider。"""


class DomainProvider(Protocol):
    domain_key: str
    task_type: str
    proposal_tool_names: frozenset[str]
    semantic_slots: frozenset[str]
    capability_tools: frozenset[str]

    def matches(self, context: DomainContext) -> bool: ...

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]: ...

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None: ...

    def completion_contract(self, tools: Sequence[str]) -> str: ...

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None: ...

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]: ...

    def prompt_specs(self) -> dict[str, ToolPromptSpec]: ...

    def is_completed_success(self, item: dict) -> bool: ...


def _successful_observations(tool_history: Sequence[dict], tool_name: str) -> list[dict]:
    result = []
    for item in tool_history:
        if item.get('tool_name') != tool_name or item.get('status') != 'success':
            continue
        try:
            payload = json.loads(item.get('observation'))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get('success', False):
            result.append(payload)
    return result


def build_expense_proposal_context(tool_history: Sequence[dict]) -> dict:
    """从当前请求的成功 Tool observation 构造 Expense 受控事实视图。"""
    travel_payloads = _successful_observations(tool_history, TRAVEL_RECORD_TOOL_NAME)
    invoice_payloads = _successful_observations(tool_history, INVOICE_VERIFY_TOOL_NAME)
    rag_payloads = _successful_observations(tool_history, RAG_TOOL_NAME)

    travel_items = []
    for payload in travel_payloads:
        items = payload.get('items', [])
        if isinstance(items, list):
            travel_items.extend(items)

    invoice_items = []
    for payload in invoice_payloads:
        if payload.get('success') is True and 'invoice_id' in payload:
            invoice_items.append(payload)
        elif payload.get('success') is True and 'items' in payload:
            invoice_items.extend(payload.get('items', []))

    policy_context = ''
    for payload in rag_payloads:
        answer = payload.get('answer')
        if isinstance(answer, str) and answer:
            policy_context = answer
            break

    return {
        'travel_record': travel_items,
        'invoices': invoice_items,
        'policy_context': policy_context,
    }


def _context_view(context: DomainContext) -> expense_input_service.ExpenseProposalContextLike:
    return expense_input_service.ExpenseProposalContextLike(
        build_expense_proposal_context(context.tool_history)
    )


class LeaveProvider:
    domain_key = 'leave'
    task_type = 'LEAVE_REQUEST'
    proposal_tool_names = frozenset({LEAVE_PROPOSAL_TOOL_NAME})
    semantic_slots = frozenset()
    capability_tools = frozenset({LEAVE_PROPOSAL_TOOL_NAME})

    def matches(self, context: DomainContext) -> bool:
        return is_annual_leave_action_intent(context.question)

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        # Leave 当前没有额外的领域依赖顺序；保留 capability gate 原集合。
        return list(tools)

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        # Leave 的现有确定性校验仍由 schema、Executor 和受控 Tool 负责。
        return None

    def completion_contract(self, tools: Sequence[str]) -> str:
        lines = ['任务完成判断补充规则：']
        if LEAVE_BALANCE_TOOL_NAME in tools:
            if LEAVE_PROPOSAL_TOOL_NAME in tools:
                lines.append(
                    f'- {LEAVE_BALANCE_TOOL_NAME} 成功只表示余额已查询；若用户目标还包含请假申请或准备申请，'
                    f'应继续调用 {LEAVE_PROPOSAL_TOOL_NAME}，不能直接 finish。'
                )
            else:
                lines.append(
                    f'- {LEAVE_BALANCE_TOOL_NAME} 成功只表示余额已查询；若用户目标还包含请假申请或准备申请，'
                    '当前能力清单没有可用受控 Proposal Tool 时不得伪造 Tool，应 finish 说明无法继续。'
                )
        for name in (LEAVE_PROPOSAL_TOOL_NAME,) if LEAVE_PROPOSAL_TOOL_NAME in tools else ():
            lines.append(
                f'- {name} 只生成待确认草稿，不执行业务写操作；成功后应选择 finish，'
                '让程序进入用户确认链路。'
            )
        return '\n'.join(lines) if len(lines) > 1 else ''

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        if (
            decision.action == 'finish'
            and LEAVE_PROPOSAL_TOOL_NAME in tools
            and LEAVE_PROPOSAL_TOOL_NAME not in {
                item.get('tool_name') for item in context.tool_history
                if item.get('status') == 'success'
            }
        ):
            raise PlannerDecisionError('finish 前未完成 leave_proposal_tool Proposal 阶段')

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        return decision, {}

    def prompt_specs(self) -> dict[str, ToolPromptSpec]:
        return {
            LEAVE_BALANCE_TOOL_NAME: ToolPromptSpec(
                description=(
                    '查询当前登录用户自己的年假余额。无参数,身份由程序层注入;'
                    '若用户未提及他人,该 Tool 是默认入口。'
                ),
                argument_contract='必须为空对象 {}；身份由程序层注入。',
                reason_code='need_balance',
                example={
                    'action': 'tool', 'tool_name': LEAVE_BALANCE_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_balance', 'expense_reason': None,
                },
                freshness_rule='年假余额必须通过当前查询获得。',
            ),
            LEAVE_REQUEST_TOOL_NAME: ToolPromptSpec(
                description=(
                    '查询当前登录用户自己已成功提交的最近请假记录(按提交时间倒序)。'
                    f'参数: limit({LEAVE_REQUEST_MIN_LIMIT}..{LEAVE_REQUEST_MAX_LIMIT},默认 20);'
                    '身份由程序层注入;暂不暴露 pending/cancelled 等状态。'
                ),
                argument_contract='只允许 {"limit": 1..50}。身份由程序层注入。',
                reason_code='need_leave_history',
                example={
                    'action': 'tool', 'tool_name': LEAVE_REQUEST_TOOL_NAME,
                    'arguments': {'limit': 10}, 'reason_code': 'need_leave_history',
                    'expense_reason': None,
                },
                freshness_rule='请假历史列表必须通过当前查询获得。',
            ),
            LEAVE_PROPOSAL_TOOL_NAME: ToolPromptSpec(
                description=(
                    '进入受控年假申请草稿链路:程序层基于用户原始问题确定性解析'
                    '日期 / 原因 / 半天等信息,生成待用户确认的申请草稿(Proposal),'
                    '不会真正提交任何写操作。无参数。'
                ),
                argument_contract=(
                    '必须为空对象 {}；日期 / 原因 / 半天等业务参数由程序层基于用户原始问题解析。'
                ),
                reason_code='need_proposal',
                example={
                    'action': 'tool', 'tool_name': LEAVE_PROPOSAL_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_proposal', 'expense_reason': None,
                },
                usage_rule=(
                    f'{LEAVE_PROPOSAL_TOOL_NAME} 使用规则:\n'
                    '- 当用户目标明确包含"申请 / 提交 / 准备 / 帮我办"年假业务动作,且所需信息'
                    '(日期、原因等)已由用户原始问题提供或已通过已有工具结果确认时,调用该 Tool。\n'
                    '- 该 Tool 只生成待用户确认的草稿(Proposal),不会提交任何写操作。\n'
                    '- 缺少必要信息(如余额不足或用户未提供日期 / 原因)时,优先 finish '
                    '告知用户补充信息或当前不可申请,不要调用该 Tool。'
                ),
            ),
        }

    def is_completed_success(self, item: dict) -> bool:
        return item.get('status') == 'success'


class ExpenseProvider:
    domain_key = 'expense'
    task_type = 'EXPENSE_REQUEST'
    proposal_tool_names = frozenset({EXPENSE_PROPOSAL_TOOL_NAME})
    semantic_slots = frozenset({'expense_reason'})
    capability_tools = frozenset({EXPENSE_PROPOSAL_TOOL_NAME})
    dependency_tools = frozenset({
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
    })

    def matches(self, context: DomainContext) -> bool:
        return bool(
            expense_input_service.is_expense_claim_intent(context.question)
            or context.continuation_original_request
        )

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        tools = list(tools)
        if not self.matches(context):
            return tools
        if context.action_proposal is not None:
            return [name for name in tools if name not in self.dependency_tools]

        reason_available = context.request_expense_reason is not None or bool(
            context.continuation_original_request and context.question.strip()
        )
        if not reason_available:
            return tools

        source_question = context.continuation_original_request or context.question
        view = _context_view(context)
        try:
            analysis = expense_input_service.analyze_expense_input(source_question, context=view)
        except expense_input_service.ExpenseInputError:
            return [
                name for name in tools
                if name not in {INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME}
            ]

        if not expense_input_service.find_trip_records(view) or analysis.trip_id is None:
            return [
                name for name in tools
                if name not in {INVOICE_VERIFY_TOOL_NAME, EXPENSE_PROPOSAL_TOOL_NAME}
            ]

        target_invoice_ids = set(analysis.invoice_ids)
        verified_invoice_ids = {
            invoice.get('invoice_id')
            for invoice in expense_input_service.find_invoice_records(view)
            if invoice.get('invoice_id')
        }
        if target_invoice_ids - verified_invoice_ids:
            return [name for name in tools if name != EXPENSE_PROPOSAL_TOOL_NAME]
        return [name for name in tools if name != INVOICE_VERIFY_TOOL_NAME]

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        if tool_name not in self.dependency_tools:
            return None
        if not self.matches(context):
            # travel_record_tool 保持旧的跨域兼容行为；invoice_verify_tool
            # 必须继续经过 selected-trip scope 第二道门，不能因非 Expense
            # prompt/continuation 而绕过边界。
            if tool_name != INVOICE_VERIFY_TOOL_NAME:
                return None
        if context.action_proposal is not None:
            raise DomainToolCallRejected(
                'expense_proposal_already_completed',
                'Expense Proposal 已存在，不能继续执行差旅/发票依赖 Tool。',
            )

        if (
            tool_name == EXPENSE_PROPOSAL_TOOL_NAME
            and not _successful_observations(context.tool_history, TRAVEL_RECORD_TOOL_NAME)
        ):
            # 没有任何当前出差事实时仍允许 Proposal 产生 trip/reason clarification；
            # 一旦已有 travel facts，则必须遵守 selected-trip/invoice 前置条件。
            return None

        legal = self.legal_tools((tool_name,), context)
        if tool_name not in legal:
            if tool_name == EXPENSE_PROPOSAL_TOOL_NAME:
                reason = 'expense_proposal_prerequisite_missing'
                message = '报销 Proposal 的出差 / 发票前置条件尚未满足，已拒绝执行。'
            else:
                reason = 'invalid_selected_trip_invoice_scope'
                message = (
                    f'invoice_id={arguments.get("invoice_id")} 不属于当前 selected trip 的 '
                    'expense_documents，或 selected trip 无法确定，已拒绝验真。'
                )
            raise DomainToolCallRejected(reason, message)

        if tool_name == INVOICE_VERIFY_TOOL_NAME:
            invoice_id = arguments.get('invoice_id')
            if not self.invoice_scope_allowed(
                context.continuation_original_request or context.question,
                context.tool_history,
                invoice_id,
            ):
                raise DomainToolCallRejected(
                    'invalid_selected_trip_invoice_scope',
                    f'invoice_id={invoice_id} 不属于当前 selected trip 的 expense_documents，'
                    '或 selected trip 无法确定，已拒绝验真。',
                )

    def invoice_scope_allowed(
        self, question: str, tool_history: Sequence[dict], invoice_id: str | None
    ) -> bool:
        view = expense_input_service.ExpenseProposalContextLike(
            build_expense_proposal_context(tool_history)
        )
        try:
            selected_trip_id = expense_input_service.analyze_expense_input(
                question, context=view
            ).trip_id
        except expense_input_service.ExpenseInputError:
            return False
        return any(
            item.get('trip_id') == selected_trip_id
            and invoice_id in {
                doc.get('invoice_id')
                for doc in (item.get('expense_documents') or [])
                if isinstance(doc, dict) and doc.get('invoice_id')
            }
            for item in expense_input_service.find_trip_records(view)
        )

    def completion_contract(self, tools: Sequence[str]) -> str:
        lines = ['任务完成判断补充规则：']
        read_tools = [
            name for name in (
                RAG_TOOL_NAME, TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME,
            ) if name in tools
        ]
        if read_tools and EXPENSE_PROPOSAL_TOOL_NAME in tools:
            lines.append(
                f'- {"、".join(read_tools)} 成功只提供报销所需事实；若用户目标还包含报销申请或准备报销，'
                f'应继续调用 {EXPENSE_PROPOSAL_TOOL_NAME}，不能直接 finish。'
            )
        if EXPENSE_PROPOSAL_TOOL_NAME in tools:
            lines.append(
                f'- {EXPENSE_PROPOSAL_TOOL_NAME} 只生成待确认草稿，不执行业务写操作；成功后应选择 finish，'
                '让程序进入用户确认链路。'
            )
            lines.append(
                '本领域 semantic slot 为 expense_reason：只从当前用户输入抽取，首次确定后本请求内冻结；'
                '不得使用出差记录的 purpose、目的地、行程描述、发票、Tool History、execution_history 或 Memory Context'
                '推断或回填，也不得把整段用户请求概括成原因。\n'
                '- expense_reason 是可选字符串；没有明确报销原因、语义有歧义或用户只是在询问应填什么时必须为 null。\n'
                '- 只从用户当前输入中明确表达的报销原因抽取，保留自然语言语义但不要复制整句请求。\n'
                '- “报销原因为客户拜访”抽取“客户拜访”；“报销原因：项目验收”抽取“项目验收”；\n'
                '  “去客户现场做项目验收”可抽取为“去客户现场做项目验收”。\n'
                '- “帮我报销最近一次客户拜访的出差”与“最近一次出差目的为客户拜访，帮我报销”都必须为 null。\n'
                '- 如果 Memory Context 或当前任务上下文明确表示系统正在等待报销原因，且用户当前输入是对该问题的\n'
                '  直接回答（例如“客户拜访”），可以抽取该回答；孤立的一句“客户拜访”没有这样的上下文时必须为 null。\n'
                '- expense_reason 不属于 arguments；调用报销 Proposal Tool 时 arguments 仍为 {}，由 Executor\n'
                '  从当前决策字段注入。'
            )
        return '\n'.join(lines) if len(lines) > 1 else ''

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        if decision.action != 'finish' or EXPENSE_PROPOSAL_TOOL_NAME not in tools:
            return None
        successful_tools = {
            item.get('tool_name') for item in context.tool_history
            if item.get('status') == 'success'
        }
        latest_proposal = next(
            (
                item for item in reversed(context.tool_history)
                if item.get('tool_name') == EXPENSE_PROPOSAL_TOOL_NAME
                and item.get('status') == 'success'
            ),
            None,
        )
        if latest_proposal is not None:
            try:
                payload = json.loads(latest_proposal.get('observation'))
            except (json.JSONDecodeError, TypeError):
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get('action_proposal') is None
                and payload.get('missing_fields', []) == ['invoice_ids']
            ):
                raise PlannerDecisionError('finish 前未完成 expense_proposal_tool Proposal 阶段')
        if EXPENSE_PROPOSAL_TOOL_NAME not in successful_tools:
            raise PlannerDecisionError('finish 前未完成 expense_proposal_tool Proposal 阶段')

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        if (
            decision.action == 'tool'
            and decision.tool_name == EXPENSE_PROPOSAL_TOOL_NAME
            and not self.matches(context)
            and RAG_TOOL_NAME in tools
        ):
            decision = PlannerDecision.model_validate({
                'action': 'tool',
                'tool_name': RAG_TOOL_NAME,
                'arguments': {'question': context.question},
                'answer': None,
                'reason_code': 'need_knowledge',
                'expense_reason': None,
            }).validate_decision()

        if (
            context.step_count == 0
            and decision.action == 'tool'
            and decision.tool_name in (TRAVEL_RECORD_TOOL_NAME, INVOICE_VERIFY_TOOL_NAME)
            and self._normalize_reason(decision.expense_reason) is None
            and expense_input_service.is_expense_claim_intent(context.question)
            and EXPENSE_PROPOSAL_TOOL_NAME in tools
        ):
            decision = PlannerDecision.model_validate({
                'action': 'tool',
                'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
                'arguments': {},
                'answer': None,
                'reason_code': 'need_expense_proposal',
                'expense_reason': None,
            }).validate_decision()

        if context.step_count == 0:
            frozen = self._normalize_reason(decision.expense_reason)
        else:
            frozen = self._normalize_reason(context.request_expense_reason)
        payload = decision.model_dump()
        payload['expense_reason'] = frozen
        return PlannerDecision.model_validate(payload).validate_decision(), {
            'request_expense_reason': frozen,
        }

    @staticmethod
    def _normalize_reason(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def is_completed_success(self, item: dict) -> bool:
        if item.get('status') != 'success':
            return False
        if item.get('tool_name') != EXPENSE_PROPOSAL_TOOL_NAME:
            return True
        try:
            payload = json.loads(item.get('observation'))
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(payload, dict) and payload.get('action_proposal') is not None

    def prompt_specs(self) -> dict[str, ToolPromptSpec]:
        return {
            TRAVEL_RECORD_TOOL_NAME: ToolPromptSpec(
                description=(
                    '查询当前登录用户自己的出差记录。返回每条 trip 及其关联的 '
                    'expense_documents(invoice reference,需单独验真)。'
                    '每个 trip 的 expense_documents 只属于该 trip，不能跨 trip 合并。'
                    '无 LLM 入参,身份与 limit 由程序层注入。'
                ),
                argument_contract='必须为空对象 {}；employee_id / limit 由程序层注入（V2 §十一）。',
                reason_code='need_travel_history',
                example={
                    'action': 'tool', 'tool_name': TRAVEL_RECORD_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_travel_history', 'expense_reason': None,
                },
                freshness_rule='如果当前决策依赖 trip 仍为 APPROVED，必须重新查询当前出差记录。',
            ),
            INVOICE_VERIFY_TOOL_NAME: ToolPromptSpec(
                description=(
                    '校验发票 / 费用凭证。LLM 仅允许传 invoice_id;employee_id 由程序层'
                    '注入并在端内做 ownership check,跨员工调用被拒绝。返回 valid / amount / '
                    'category / duplicate 等字段。'
                ),
                argument_contract='只允许 {"invoice_id": "..."}；employee_id 不得由 LLM 提供（V2 §十一）。',
                reason_code='need_invoice_verify',
                example={
                    'action': 'tool', 'tool_name': INVOICE_VERIFY_TOOL_NAME,
                    'arguments': {'invoice_id': 'INV-001'},
                    'reason_code': 'need_invoice_verify', 'expense_reason': None,
                },
                freshness_rule='如果当前决策依赖发票 valid / duplicate，必须重新调用发票验真。',
            ),
            EXPENSE_PROPOSAL_TOOL_NAME: ToolPromptSpec(
                description=(
                    '进入受控报销草稿链路:程序层基于 tool_history 中已成功完成的 '
                    'travel / invoice / RAG 事实抽取 ExpenseProposalContext；Planner 仅通过独立的 '
                    'expense_reason 字段提供用户语义上的报销原因，生成待用户确认的报销申请草稿'
                    '(ExpenseActionProposal),不会提交任何写操作。arguments 仍必须为空对象。'
                ),
                argument_contract=(
                    '必须为空对象 {}；业务事实由程序层从 tool_history 注入；expense_reason '
                    '是独立的 Planner 决策字段，不得放入 arguments。'
                ),
                reason_code='need_expense_proposal',
                example={
                    'action': 'tool', 'tool_name': EXPENSE_PROPOSAL_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_expense_proposal', 'expense_reason': None,
                },
                usage_rule=(
                    f'{EXPENSE_PROPOSAL_TOOL_NAME} 使用规则:\n'
                    '- 仅当用户明确要求办理、准备、发起或提交报销申请等业务动作时使用；'
                    '“报销流程是什么”“报销需要什么材料”“报销原因应该填什么”等咨询必须调用 '
                    f'{RAG_TOOL_NAME}，不得进入报销 Proposal 或 reason clarification。\n'
                    '- expense_reason 缺失时应优先调用该 Tool 产生 reason clarification；不要先调用 '
                    f'{TRAVEL_RECORD_TOOL_NAME} 或 {INVOICE_VERIFY_TOOL_NAME} 收集其它字段。\n'
                    '- 用户要求“对应发票 / 相关发票 / 全部发票”时，若返回多条 trip，必须先根据用户 selector '
                    '选出唯一 selected trip；每个 trip 的 expense_documents 只属于该 trip，不能跨 trip 合并。\n'
                    f'- {TRAVEL_RECORD_TOOL_NAME} 成功后，只要 selected trip 的 expense_documents 仍有任一 invoice\n'
                    '  未成功验真，'
                    f'下一步只能从该 selected trip 的未验真 invoice 中选择 {INVOICE_VERIFY_TOOL_NAME}；验真顺序不限。\n'
                    f'- {INVOICE_VERIFY_TOOL_NAME} 的范围严格等于 selected trip 的 expense_documents；只对其中的 '
                    'invoice_id 验真，不得验证其它 trip 的 invoice references，也不得为了“完整检查”继续调用。\n'
                    f'- selected trip 的 expense_documents 全部成功验真后，必须立即调用 '
                    f'{EXPENSE_PROPOSAL_TOOL_NAME}；selected trip 没有 expense_documents 时不得借用其它 trip 的发票。\n'
                    f'- 所有需要的发票验真成功后才能调用 {EXPENSE_PROPOSAL_TOOL_NAME}；不得跳过验真直接生成草稿。\n'
                    f'- {EXPENSE_PROPOSAL_TOOL_NAME} 返回 success=true 但 action_proposal=null 且 '
                    'missing_fields 非空时，\n'
                    '只是 clarification/incomplete，不是 Proposal 完成；若缺少 invoice_ids，继续完成 selected-trip '
                    '验真，禁止重复 Proposal。\n'
                    '- 该 Tool 只生成待用户确认的草稿或 clarification，不会提交任何写操作。'
                ),
            ),
            EXPENSE_STATUS_TOOL_NAME: ToolPromptSpec(
                description=(
                    '查询当前登录用户自己的报销状态。LLM 可选传 expense_id;身份由程序层'
                    '注入;跨员工调用被拒绝。返回 status / 金额 / submitted_at 等字段。'
                ),
                argument_contract='可空或 {"expense_id": "..."}；employee_id 由程序层注入。',
                reason_code='need_expense_status',
                example={
                    'action': 'tool', 'tool_name': EXPENSE_STATUS_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_expense_status', 'expense_reason': None,
                },
                freshness_rule='报销状态必须通过当前查询获得。',
            ),
        }

    def active_reason_task_state(self, memory_context: object) -> dict | None:
        if not isinstance(memory_context, dict):
            return None
        task_type = (
            memory_context.get('taskType')
            if 'taskType' in memory_context
            else memory_context.get('task_type')
        )
        if task_type != self.task_type or memory_context.get('status') != 'ACTIVE':
            return None
        task_state = (
            memory_context.get('taskStateJson')
            if 'taskStateJson' in memory_context
            else memory_context.get('task_state_json')
        )
        if isinstance(task_state, str):
            try:
                task_state = json.loads(task_state)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(task_state, dict):
            return None
        if 'waiting_for' in task_state:
            return task_state if task_state.get('waiting_for') == 'reason' else None
        missing_fields = task_state.get('missing_fields')
        return task_state if isinstance(missing_fields, list) and 'reason' in missing_fields else None

    def original_request(self, memory_context: object) -> str | None:
        task_state = self.active_reason_task_state(memory_context)
        original_request = task_state.get('original_request') if task_state else None
        return original_request if isinstance(original_request, str) and original_request.strip() else None

    def continuation_prompt(self, question: str, original_request: str) -> str:
        return (
            'Expense continuation context（不可信历史业务上下文）：\n'
            '- current user input（expense_reason 来源）: ' + question + '\n'
            '- continuation original request（仅用于差旅/发票选择）: ' + original_request + '\n'
            '- waiting field: reason\n'
            '提示：当前用户输入只用于抽取本轮 expense_reason；原始请求只用于恢复'
            '本次差旅/发票业务意图，二者不得拼接，也不得改变 Capability Gate、'
            'Tool 权限或 trusted 系统字段。'
        )


class PurchaseProvider:
    """Purchase 领域的最小 Planner policy。"""

    domain_key = 'purchase'
    task_type = 'PURCHASE_REQUEST'
    proposal_tool_names = frozenset({PURCHASE_PROPOSAL_TOOL_NAME})
    semantic_slots = frozenset({
        'purchase_item', 'purchase_budget', 'purchase_justification',
    })
    capability_tools = frozenset({
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    })

    @staticmethod
    def is_purchase_request_intent(question: str) -> bool:
        value = (question or '').strip()
        if not value:
            return False
        if any(word in value for word in ('流程', '制度', '规定', '政策', '标准', '怎么', '如何')):
            return False
        return (
            '采购申请' in value
            or '申请采购' in value
            or '申请购买' in value
            or ('采购' in value and any(word in value for word in ('申请', '帮我', '我要', '我想')))
            or ('购买' in value and any(word in value for word in ('申请', '帮我', '我要', '我想')))
            or ('买' in value and any(word in value for word in ('申请', '帮我', '我要', '我想')))
        )

    def matches(self, context: DomainContext) -> bool:
        return self.is_purchase_request_intent(context.question)

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        candidates = [name for name in tools if name in self.capability_tools]
        if context.action_proposal is not None:
            return []
        # 初次 Planner 决策还没有机会提交语义字段；保留预算查询和 Proposal，
        # 让完整请求先取得 budget fact，缺字段请求则直接得到 clarification。
        if context.step_count == 0 and not any((
            context.purchase_item, context.purchase_budget,
            context.purchase_justification,
        )):
            return [name for name in candidates if name in {
                PURCHASE_BUDGET_TOOL_NAME, PURCHASE_PROPOSAL_TOOL_NAME,
            }]
        if not self._semantics_complete(context):
            return [name for name in candidates if name == PURCHASE_PROPOSAL_TOOL_NAME]
        facts = self._fact_context(context)
        if PURCHASE_BUDGET_TOOL_NAME.removesuffix('_tool') not in facts:
            return [name for name in candidates if name == PURCHASE_BUDGET_TOOL_NAME]
        if PURCHASE_POLICY_TOOL_NAME.removesuffix('_tool') not in facts:
            return [name for name in candidates if name == PURCHASE_POLICY_TOOL_NAME]
        return [name for name in candidates if name == PURCHASE_PROPOSAL_TOOL_NAME]

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        if tool_name not in self.capability_tools:
            return None
        if not self.matches(context):
            raise DomainToolCallRejected(
                'purchase_intent_required', '当前请求不是可识别的采购申请。'
            )
        if tool_name == PURCHASE_BUDGET_TOOL_NAME:
            return None
        if tool_name == PURCHASE_POLICY_TOOL_NAME and not self._semantics_complete(context):
            raise DomainToolCallRejected(
                'purchase_semantic_fields_missing',
                '采购政策查询需要 item、requested budget 和 justification。',
            )
        if tool_name == PURCHASE_PROPOSAL_TOOL_NAME:
            if not self._semantics_complete(context):
                return None
            if not self._all_facts_present(context):
                raise DomainToolCallRejected(
                    'purchase_prerequisite_missing',
                    '采购 Proposal 的预算和政策事实尚未完成查询。',
                )

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        if decision.action != 'finish' or PURCHASE_PROPOSAL_TOOL_NAME not in tools:
            return None
        latest = next(
            (
                item for item in reversed(context.tool_history)
                if item.get('tool_name') == PURCHASE_PROPOSAL_TOOL_NAME
                and item.get('status') == 'success'
            ),
            None,
        )
        if latest is None:
            raise PlannerDecisionError('finish 前未完成 purchase_proposal_tool Proposal 阶段')
        try:
            payload = json.loads(latest.get('observation'))
        except (json.JSONDecodeError, TypeError):
            payload = None
        if not isinstance(payload, dict) or payload.get('success') is not True:
            raise PlannerDecisionError('finish 前未完成 purchase_proposal_tool Proposal 阶段')
        if payload.get('kind') not in {'proposal', 'clarification', 'rejection'}:
            raise PlannerDecisionError('finish 前未完成 purchase_proposal_tool Proposal 阶段')

    def completion_contract(self, tools: Sequence[str]) -> str:
        if PURCHASE_PROPOSAL_TOOL_NAME not in tools:
            return ''
        return (
            '采购任务完成判断补充规则：\n'
            f'- {PURCHASE_PROPOSAL_TOOL_NAME} 只生成待确认草稿或确定性澄清/拒绝结果，'
            '不执行业务写操作；成功后应选择 finish，让程序进入用户确认链路。'
        )

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        if context.step_count == 0:
            item = self._normalize_text(decision.purchase_item)
            budget = self._normalize_budget(decision.purchase_budget)
            justification = self._normalize_text(decision.purchase_justification)
        else:
            item = context.purchase_item
            budget = context.purchase_budget
            justification = context.purchase_justification
        payload = decision.model_dump()
        payload.update({
            'purchase_item': item,
            'purchase_budget': budget,
            'purchase_justification': justification,
        })
        return PlannerDecision.model_validate(payload).validate_decision(), {
            'purchase_item': item,
            'purchase_budget': budget,
            'purchase_justification': justification,
        }

    def prompt_specs(self) -> dict[str, ToolPromptSpec]:
        return {
            PURCHASE_BUDGET_TOOL_NAME: ToolPromptSpec(
                description='查询当前登录员工的确定性可用采购预算；无参数，身份由程序层注入。',
                argument_contract='必须为空对象 {}；employee_id 由程序层注入。',
                reason_code='need_purchase_budget',
                example={
                    'action': 'tool', 'tool_name': PURCHASE_BUDGET_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_purchase_budget',
                    'purchase_item': '开发用 MacBook', 'purchase_budget': 15000,
                    'purchase_justification': '移动端开发',
                },
                freshness_rule='当前可用采购预算必须通过本次 purchase_budget_tool 查询。',
            ),
            PURCHASE_POLICY_TOOL_NAME: ToolPromptSpec(
                description='根据程序注入的 item、预算和 justification 查询确定性采购政策；无 LLM 参数。',
                argument_contract='必须为空对象 {}；Purchase 语义字段由程序层注入。',
                reason_code='need_purchase_policy',
                example={
                    'action': 'tool', 'tool_name': PURCHASE_POLICY_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_purchase_policy',
                    'purchase_item': '开发用 MacBook', 'purchase_budget': 15000,
                    'purchase_justification': '移动端开发',
                },
                freshness_rule='当前采购政策必须通过本次 purchase_policy_tool 查询。',
            ),
            PURCHASE_PROPOSAL_TOOL_NAME: ToolPromptSpec(
                description='只根据用户语义字段和已查询的确定性预算/政策事实生成待确认 Purchase Proposal；不写库。',
                argument_contract='必须为空对象 {}；item、预算和 justification 是独立语义字段，不得放入 arguments。',
                reason_code='need_purchase_proposal',
                example={
                    'action': 'tool', 'tool_name': PURCHASE_PROPOSAL_TOOL_NAME,
                    'arguments': {}, 'reason_code': 'need_purchase_proposal',
                    'purchase_item': '开发用 MacBook', 'purchase_budget': 15000,
                    'purchase_justification': '移动端开发',
                },
                usage_rule=(
                    f'{PURCHASE_PROPOSAL_TOOL_NAME} 使用规则:\n'
                    '- 每次 Purchase Tool 决策都必须在 JSON 顶层输出 purchase_item、purchase_budget、'
                    'purchase_justification；它们只从当前用户任务抽取，不得放入 arguments。已提供的值不能省略，'
                    '缺失的槽才输出 null。\n'
                    '- 例如“帮我申请购买一台开发用 MacBook，预算15000，原因是移动端开发”应输出：'
                    'purchase_item="开发用 MacBook"、purchase_budget=15000、'
                    'purchase_justification="移动端开发"。\n'
                    '- 用户明确要求申请购买时使用；只生成待确认草稿，不写 purchase_request。\n'
                    '- item、requested budget、justification 任一缺失时直接调用该 Tool 产生 clarification，'
                    '不要伪造缺失字段。\n'
                    '- 语义字段完整时，必须先取得 purchase_budget_tool 和 purchase_policy_tool 的成功事实，'
                    '不得相信 LLM 自报 available_budget 或 policy_result。'
                ),
            ),
        }

    def is_completed_success(self, item: dict) -> bool:
        if item.get('status') != 'success':
            return False
        if item.get('tool_name') != PURCHASE_PROPOSAL_TOOL_NAME:
            return True
        try:
            payload = json.loads(item.get('observation'))
        except (json.JSONDecodeError, TypeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get('success') is True
            and payload.get('kind') in {'proposal', 'clarification', 'rejection'}
        )

    @staticmethod
    def _normalize_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @staticmethod
    def _normalize_budget(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            budget = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if not budget.is_finite() or budget <= 0:
            return None
        return budget

    def _semantics_complete(self, context: DomainContext) -> bool:
        return bool(
            context.purchase_item
            and context.purchase_budget is not None
            and context.purchase_justification
        )

    @staticmethod
    def _fact_context(context: DomainContext) -> dict[str, dict[str, Any]]:
        from app.services.purchase_facts_service import purchase_fact_context

        return purchase_fact_context(context.tool_history)

    def _all_facts_present(self, context: DomainContext) -> bool:
        facts = self._fact_context(context)
        return (
            PURCHASE_BUDGET_TOOL_NAME.removesuffix('_tool') in facts
            and PURCHASE_POLICY_TOOL_NAME.removesuffix('_tool') in facts
        )


_PLATFORM_PROMPT_SPECS = {
    RAG_TOOL_NAME: ToolPromptSpec(
        description='回答企业制度、流程、IT/HR 文档等知识库问题。参数: question(用户问题)。',
        argument_contract='只允许 {"question": "用户问题"}。',
        reason_code='need_knowledge',
        example={
            'action': 'tool', 'tool_name': RAG_TOOL_NAME,
            'arguments': {'question': '公司的年假制度是什么'},
            'reason_code': 'need_knowledge', 'expense_reason': None,
        },
    ),
    EVAL_TOOL_NAME: ToolPromptSpec(
        description='查询 RAG 评估报告。参数: report_type(retrieval|generation|all)。',
        argument_contract='只允许 {"report_type": "retrieval"|"generation"|"all"}。',
        reason_code='need_eval',
        example={
            'action': 'tool', 'tool_name': EVAL_TOOL_NAME,
            'arguments': {'report_type': 'all'},
            'reason_code': 'need_eval', 'expense_reason': None,
        },
    ),
}


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
            overlap = self._prompt_specs.keys() & provider.prompt_specs().keys()
            if overlap:
                raise ValueError(f'Domain Provider Tool prompt 重复注册: {sorted(overlap)}')
            self._prompt_specs.update(provider.prompt_specs())

    @property
    def providers(self) -> tuple[DomainProvider, ...]:
        return self._providers

    @property
    def business_action_tools(self) -> frozenset[str]:
        """所有领域声明的业务 capability Tool；不做动态 discovery。"""
        return frozenset(
            name for provider in self._providers
            for name in provider.capability_tools
        )

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
        matches = tuple(
            provider for provider in self._providers
            if tool_name in provider.proposal_tool_names
            or tool_name in provider.capability_tools
            or tool_name in getattr(provider, 'dependency_tools', frozenset())
            or tool_name in provider.prompt_specs()
            and provider.domain_key != 'leave'
        )
        if len(matches) > 1:
            raise DomainProviderAmbiguityError(
                f'Tool {tool_name} 同时属于多个业务领域'
            )
        return matches[0] if matches else None

    def legal_tools(self, tools: Sequence[str], context: DomainContext) -> list[str]:
        provider = self.resolve(context)
        if provider is None:
            return list(tools)
        original = list(tools)
        legal = provider.legal_tools(original, context)
        allowed = set(original)
        return [name for name in legal if name in allowed]

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        provider = self.provider_for_tool(tool_name)
        if provider is not None:
            provider.validate_tool_call(tool_name, arguments, context)

    def validate_completion(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> None:
        provider = self.resolve(context)
        if provider is not None:
            provider.validate_completion(decision, tools, context)

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
            'finish 前未完成 expense_proposal_tool Proposal 阶段': (
                'planner_completion_validation', 'expense_proposal_missing',
                '当前用户目标包含报销申请，但尚未完成 expense_proposal_tool；请继续规划剩余目标。',
            ),
            'finish 前未完成 purchase_proposal_tool Proposal 阶段': (
                'planner_completion_validation', 'purchase_proposal_missing',
                '当前用户目标包含采购申请，但尚未完成 purchase_proposal_tool；请继续规划剩余目标。',
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


# P4-1 intentionally uses a small, explicit static registry. Adding a future
# domain requires a deliberate registration and schema/tool review.
DOMAIN_PROVIDER_REGISTRY = DomainProviderRegistry(
    (ExpenseProvider(), LeaveProvider(), PurchaseProvider())
)
