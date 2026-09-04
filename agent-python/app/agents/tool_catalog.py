"""Planner-facing Tool semantic metadata.

The catalog deliberately contains only the information used to describe a
Tool to the Planner. Executable ToolSpec data remains in
``tool_executor_node``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

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
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
)


@dataclass(frozen=True)
class ToolPromptSpec:
    """Semantic metadata rendered into the Planner system prompt."""

    description: str
    argument_contract: str
    reason_code: str
    example: dict[str, Any]
    usage_rule: str = ''
    freshness_rule: str = ''
    name: str = ''
    domain: str | None = None
    side_effect: str = 'NONE'


class ToolCatalog:
    """Explicit, duplicate-checked catalog of Planner Tool metadata."""

    def __init__(self, specs: Sequence[ToolPromptSpec]):
        names = [spec.name for spec in specs]
        if any(not name for name in names):
            raise ValueError('ToolCatalog 中每个 ToolPromptSpec 都必须声明 name')
        if len(set(names)) != len(names):
            raise ValueError('ToolCatalog 不允许重复 tool name')
        self._specs = {spec.name: spec for spec in specs}

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    @property
    def specs(self) -> dict[str, ToolPromptSpec]:
        return dict(self._specs)

    def prompt_spec(self, tool_name: str) -> ToolPromptSpec:
        return self._specs[tool_name]

    def specs_for_domain(self, domain: str | None) -> dict[str, ToolPromptSpec]:
        return {
            name: spec
            for name, spec in self._specs.items()
            if spec.domain == domain
        }


TOOL_CATALOG = ToolCatalog((
    ToolPromptSpec(
        name=RAG_TOOL_NAME,
        domain=None,
        description='回答企业制度、流程、IT/HR 文档等知识库问题。参数: question(用户问题)。',
        argument_contract='只允许 {"question": "用户问题"}。',
        reason_code='need_knowledge',
        example={
            'action': 'tool', 'tool_name': RAG_TOOL_NAME,
            'arguments': {'question': '公司的年假制度是什么'},
            'reason_code': 'need_knowledge', 'expense_reason': None,
        },
    ),
    ToolPromptSpec(
        name=EVAL_TOOL_NAME,
        domain=None,
        description='查询 RAG 评估报告。参数: report_type(retrieval|generation|all)。',
        argument_contract='只允许 {"report_type": "retrieval"|"generation"|"all"}。',
        reason_code='need_eval',
        example={
            'action': 'tool', 'tool_name': EVAL_TOOL_NAME,
            'arguments': {'report_type': 'all'},
            'reason_code': 'need_eval', 'expense_reason': None,
        },
    ),
    ToolPromptSpec(
        name=LEAVE_BALANCE_TOOL_NAME,
        domain='leave',
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
    ToolPromptSpec(
        name=LEAVE_REQUEST_TOOL_NAME,
        domain='leave',
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
    ToolPromptSpec(
        name=LEAVE_PROPOSAL_TOOL_NAME,
        domain='leave',
        side_effect='PROPOSAL',
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
    ToolPromptSpec(
        name=TRAVEL_RECORD_TOOL_NAME,
        domain='expense',
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
    ToolPromptSpec(
        name=INVOICE_VERIFY_TOOL_NAME,
        domain='expense',
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
    ToolPromptSpec(
        name=EXPENSE_PROPOSAL_TOOL_NAME,
        domain='expense',
        side_effect='PROPOSAL',
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
    ToolPromptSpec(
        name=EXPENSE_STATUS_TOOL_NAME,
        domain='expense',
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
))


# Kept as a small compatibility name for code/tests that used the old module
# local constant. The actual definitions live in TOOL_CATALOG above.
_PLATFORM_PROMPT_SPECS = TOOL_CATALOG.specs_for_domain(None)
