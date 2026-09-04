"""Planner-facing Tool semantic metadata.

The catalog deliberately contains only the information used to describe a
Tool to the Planner. Executable ToolSpec data remains in
``tool_executor_node``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence, TypedDict

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

CapabilityCategory = Literal[
    'enterprise_knowledge',
    'personal_realtime_data',
    'business_action',
    'eval',
]
CapabilityAvailability = Literal['available', 'unavailable']
CAPABILITY_CATEGORIES: tuple[CapabilityCategory, ...] = (
    'enterprise_knowledge',
    'personal_realtime_data',
    'business_action',
    'eval',
)
CAPABILITY_AVAILABLE: CapabilityAvailability = 'available'
CAPABILITY_UNAVAILABLE: CapabilityAvailability = 'unavailable'


class CapabilityStatus(TypedDict):
    enterprise_knowledge: CapabilityAvailability
    personal_realtime_data: CapabilityAvailability
    business_action: CapabilityAvailability
    eval: CapabilityAvailability


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
    capability_category: CapabilityCategory | None = None


class ToolCatalog:
    """Explicit, duplicate-checked catalog of Planner Tool metadata."""

    def __init__(self, specs: Sequence[ToolPromptSpec]):
        names = [spec.name for spec in specs]
        if any(not name for name in names):
            raise ValueError('ToolCatalog 中每个 ToolPromptSpec 都必须声明 name')
        if len(set(names)) != len(names):
            raise ValueError('ToolCatalog 不允许重复 tool name')
        if any(spec.capability_category not in CAPABILITY_CATEGORIES for spec in specs):
            raise ValueError('ToolCatalog 中每个 ToolPromptSpec 都必须声明合法 capability category')
        self._specs = {spec.name: spec for spec in specs}

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    @property
    def specs(self) -> dict[str, ToolPromptSpec]:
        return dict(self._specs)

    def prompt_spec(self, tool_name: str) -> ToolPromptSpec:
        return self._specs[tool_name]

    def capability_status(self, authorized_tools: Sequence[str]) -> CapabilityStatus:
        """从 Capability Gate 已授权 Tool 集合确定性汇总能力类别状态。

        该摘要不增加权限，也不读取用户问题；未知 Tool 直接失败，避免状态摘要
        与 Tool Catalog 脱节。
        """
        unknown = set(authorized_tools) - set(self._specs)
        if unknown:
            raise ValueError(f'Capability Status 收到未注册 Tool: {sorted(unknown)}')
        authorized = set(authorized_tools)
        return {
            category: (
                CAPABILITY_AVAILABLE
                if any(
                    name in authorized
                    and self._specs[name].capability_category == category
                    for name in self._specs
                )
                else CAPABILITY_UNAVAILABLE
            )
            for category in CAPABILITY_CATEGORIES
        }


TOOL_CATALOG = ToolCatalog((
    ToolPromptSpec(
        name=RAG_TOOL_NAME,
        domain=None,
        capability_category='enterprise_knowledge',
        description=(
            '回答企业制度、流程、IT/HR 文档等静态知识库问题。'
            '不用于替代个人实时、身份绑定的余额、状态、历史或记录查询；'
            '若当前能力清单没有对应的个人实时能力，不得用该 Tool 猜测或代答。'
            '参数: question(用户问题)。'
        ),
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
        capability_category='eval',
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
        capability_category='personal_realtime_data',
        description=(
            '只查询当前登录用户本人的当前剩余/可用年假余额、可休天数等实时事实。'
            '不回答公司制度或申请流程,不查询请假历史,也不创建或准备申请。'
            '用户明确要求办理、准备、创建或发起具体年假申请时,不能因为申请前可能需要了解余额'
            '就选择此 Tool;应使用当前能力清单中可见的 business-action Proposal Tool。'
            '无参数,身份由程序层注入。'
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
        capability_category='personal_realtime_data',
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
        capability_category='business_action',
        description=(
            '仅用于用户明确要求系统办理、准备、创建或发起一个具体年假申请时进入受控'
            '年假申请草稿链路;程序层基于用户原始问题确定性解析日期 / 原因 / 半天等信息,'
            '生成待用户确认的申请草稿(Proposal),不会真正提交任何写操作；'
            '即使缺少必要字段,也由该 Tool 返回结构化 clarification 与续接状态。'
            '“年假制度是什么”“年假怎么申请”等知识或流程咨询属于 RAG;'
            '只查询本人当前余额属于 leave_balance_tool。无参数。'
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
            '- 当用户目标明确包含"申请 / 提交 / 准备 / 帮我办"年假业务动作时调用该 Tool；'
            '即使缺少日期 / 原因等字段,也进入结构化 clarification,不要因为缺字段而 refuse。\n'
            f'- 不要把具体年假申请目标误判为 {LEAVE_BALANCE_TOOL_NAME};只有用户目标本身是查询'
            f'本人当前余额时才使用 {LEAVE_BALANCE_TOOL_NAME}。\n'
            f'- “年假制度是什么”“年假怎么申请”等知识或流程咨询使用 {RAG_TOOL_NAME},'
            f'不使用 {LEAVE_PROPOSAL_TOOL_NAME}。\n'
            '- 该 Tool 只生成待用户确认的草稿(Proposal),不会提交任何写操作。\n'
            '- Tool 会在缺少日期 / 原因等字段时返回 missing_fields 与 continuation_state，'
            '由产品层提示用户补充；余额不足或业务规则不允许时,按 Tool 结果 finish 说明无法继续。'
        ),
    ),
    ToolPromptSpec(
        name=TRAVEL_RECORD_TOOL_NAME,
        domain='expense',
        capability_category='personal_realtime_data',
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
        capability_category='personal_realtime_data',
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
        capability_category='business_action',
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
        capability_category='personal_realtime_data',
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
