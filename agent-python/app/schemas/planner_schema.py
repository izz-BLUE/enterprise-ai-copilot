"""
planner_schema.py —— Planner 决策数据结构与确定性校验

Planner 只拥有"规划权":模型输出严格结构化的"下一步决策",
程序层负责字段一致性校验与权限校验。
系统字段(trace_id / original_question / 权限字段 / 业务身份信息)
不属于模型可控范围,由程序层在 Tool 执行时注入。
"""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Action = Literal['tool', 'finish', 'refuse']
ToolName = Literal[
    'rag_answer_tool',
    'eval_report_tool',
    'leave_balance_tool',
    'leave_request_tool',
    'leave_proposal_tool',
    # P2-A Expense Workflow V1: 4 个新 Tool
    'travel_record_tool',
    'invoice_verify_tool',
    'expense_proposal_tool',
    'expense_status_tool',
    'purchase_budget_tool',
    'purchase_policy_tool',
    'purchase_proposal_tool',
]
ReasonCode = Literal[
    'need_knowledge',
    'need_eval',
    'need_balance',
    'need_leave_history',
    'need_proposal',
    'need_travel_history',
    'need_invoice_verify',
    'need_expense_proposal',
    'need_expense_status',
    'need_purchase_budget',
    'need_purchase_policy',
    'need_purchase_proposal',
    'task_complete',
    'not_allowed',
    'cannot_complete',
]

RAG_TOOL_NAME = 'rag_answer_tool'
EVAL_TOOL_NAME = 'eval_report_tool'
LEAVE_BALANCE_TOOL_NAME = 'leave_balance_tool'
LEAVE_REQUEST_TOOL_NAME = 'leave_request_tool'
LEAVE_PROPOSAL_TOOL_NAME = 'leave_proposal_tool'
# P2-A Expense Workflow V1: 4 个新 Tool
TRAVEL_RECORD_TOOL_NAME = 'travel_record_tool'
INVOICE_VERIFY_TOOL_NAME = 'invoice_verify_tool'
EXPENSE_PROPOSAL_TOOL_NAME = 'expense_proposal_tool'
EXPENSE_STATUS_TOOL_NAME = 'expense_status_tool'
PURCHASE_BUDGET_TOOL_NAME = 'purchase_budget_tool'
PURCHASE_POLICY_TOOL_NAME = 'purchase_policy_tool'
PURCHASE_PROPOSAL_TOOL_NAME = 'purchase_proposal_tool'
VALID_REPORT_TYPES = ('retrieval', 'generation', 'all')
LEAVE_REQUEST_MIN_LIMIT = 1
LEAVE_REQUEST_MAX_LIMIT = 50

# Tool arguments 严格白名单:模型只能生成业务参数,不能夹带系统控制字段。
# leave_proposal_tool 不接受任何 LLM 入参:原始问题 / business_date / trace_id
# 等系统字段均由 Executor 注入;模型不可在 arguments 中夹带日期 / 原因等。
_RAG_TOOL_ARG_KEYS = frozenset({'question'})
_EVAL_TOOL_ARG_KEYS = frozenset({'report_type'})
_LEAVE_BALANCE_ARG_KEYS = frozenset()  # 无 LLM 入参;身份全部由 Executor 注入
_LEAVE_REQUEST_ARG_KEYS = frozenset({'limit'})
_LEAVE_PROPOSAL_ARG_KEYS = frozenset()  # 不接受 LLM 入参;由 Executor 从原始问题解析
# P2-A: travel_record_tool 与 expense_status_tool 都不接受 LLM 入参;
# invoice_verify_tool 强制 identity_required=true，LLM 仅能传 invoice_id。
_TRAVEL_RECORD_ARG_KEYS = frozenset()
_INVOICE_VERIFY_ARG_KEYS = frozenset({'invoice_id'})
_EXPENSE_PROPOSAL_ARG_KEYS = frozenset()  # Phase 7: 不接受 LLM 入参
_EXPENSE_STATUS_ARG_KEYS = frozenset({'expense_id'})  # 可选 LLM 入参（按 expense_id 查询）
_PURCHASE_BUDGET_ARG_KEYS = frozenset()
_PURCHASE_POLICY_ARG_KEYS = frozenset()
_PURCHASE_PROPOSAL_ARG_KEYS = frozenset()


class PlannerDecisionError(ValueError):
    """PlannerDecision 字段一致性校验失败。"""


class PlannerDecision(BaseModel):
    """严格的"下一步决策"结构。

    约束(由 validate_decision 确定性校验,非法结构抛错而非静默修复):
      action == tool   → tool_name 非空、arguments 满足对应工具的必需参数
      action == finish → tool_name 为 None、answer 非空
      action == refuse → tool_name 为 None、answer 非空
    """

    model_config = ConfigDict(extra='forbid')

    action: Action
    tool_name: ToolName | None = None
    arguments: dict[str, Any] | None = None
    answer: str | None = None
    reason_code: ReasonCode
    # 仅由 Planner 从用户当前语义中抽取；不是 Tool arguments，也不是 trusted
    # system field。expense_proposal_tool 执行时由 Executor 单独注入。
    expense_reason: str | None = None
    # Purchase 领域语义字段；它们不是 trusted facts，也不进入 Tool arguments。
    purchase_item: str | None = None
    purchase_budget: Decimal | None = None
    purchase_justification: str | None = None

    def validate_decision(self) -> 'PlannerDecision':
        """确定性字段一致性校验;非法结构抛 PlannerDecisionError,不静默修复。"""
        if self.action == 'tool':
            if self.tool_name is None:
                raise PlannerDecisionError('action=tool 必须提供 tool_name')
            if self.tool_name == RAG_TOOL_NAME:
                self._validate_rag()
            elif self.tool_name == EVAL_TOOL_NAME:
                self._validate_eval()
            elif self.tool_name == LEAVE_BALANCE_TOOL_NAME:
                self._validate_leave_balance()
            elif self.tool_name == LEAVE_REQUEST_TOOL_NAME:
                self._validate_leave_request()
            elif self.tool_name == LEAVE_PROPOSAL_TOOL_NAME:
                self._validate_leave_proposal()
            elif self.tool_name == TRAVEL_RECORD_TOOL_NAME:
                self._validate_travel_record()
            elif self.tool_name == INVOICE_VERIFY_TOOL_NAME:
                self._validate_invoice_verify()
            elif self.tool_name == EXPENSE_PROPOSAL_TOOL_NAME:
                self._validate_expense_proposal()
            elif self.tool_name == EXPENSE_STATUS_TOOL_NAME:
                self._validate_expense_status()
            elif self.tool_name == PURCHASE_BUDGET_TOOL_NAME:
                self._validate_purchase_budget()
            elif self.tool_name == PURCHASE_POLICY_TOOL_NAME:
                self._validate_purchase_policy()
            elif self.tool_name == PURCHASE_PROPOSAL_TOOL_NAME:
                self._validate_purchase_proposal()
            else:
                raise PlannerDecisionError(f'未知 tool_name: {self.tool_name}')
        else:
            if self.tool_name is not None:
                raise PlannerDecisionError(f'action={self.action} 不得携带 tool_name')
            if self.arguments is not None:
                raise PlannerDecisionError(f'action={self.action} 不得携带 arguments')
            if not self.answer or not self.answer.strip():
                raise PlannerDecisionError(f'action={self.action} 必须提供非空 answer')
            if self.action == 'finish' and self.reason_code != 'task_complete':
                raise PlannerDecisionError('finish 的 reason_code 必须是 task_complete')
            if self.action == 'refuse' and self.reason_code not in ('not_allowed', 'cannot_complete'):
                raise PlannerDecisionError(
                    'refuse 的 reason_code 只能是 not_allowed 或 cannot_complete'
                )
        return self

    def _validate_rag(self) -> None:
        if not isinstance(self.arguments, dict) or not self.arguments:
            raise PlannerDecisionError('action=tool 必须提供非空 arguments')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _RAG_TOOL_ARG_KEYS:
            raise PlannerDecisionError(
                f'rag_answer_tool 的 arguments 只允许字段 {sorted(_RAG_TOOL_ARG_KEYS)}'
            )
        question = self.arguments['question']
        if not isinstance(question, str) or not question.strip():
            raise PlannerDecisionError('rag_answer_tool 必须提供非空 question 参数')
        if self.reason_code != 'need_knowledge':
            raise PlannerDecisionError('rag_answer_tool 的 reason_code 必须是 need_knowledge')

    def _validate_eval(self) -> None:
        if not isinstance(self.arguments, dict) or not self.arguments:
            raise PlannerDecisionError('action=tool 必须提供非空 arguments')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _EVAL_TOOL_ARG_KEYS:
            raise PlannerDecisionError(
                f'eval_report_tool 的 arguments 只允许字段 {sorted(_EVAL_TOOL_ARG_KEYS)}'
            )
        if self.arguments['report_type'] not in VALID_REPORT_TYPES:
            raise PlannerDecisionError(
                f'eval_report_tool 的 report_type 必须是 {VALID_REPORT_TYPES} 之一'
            )
        if self.reason_code != 'need_eval':
            raise PlannerDecisionError('eval_report_tool 的 reason_code 必须是 need_eval')

    def _validate_leave_balance(self) -> None:
        # arguments 必须存在但允许为空 dict,身份字段全部由 Executor 注入。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _LEAVE_BALANCE_ARG_KEYS:
            raise PlannerDecisionError(
                'leave_balance_tool 不接受任何 LLM 参数;身份由程序层注入'
            )
        if self.reason_code != 'need_balance':
            raise PlannerDecisionError('leave_balance_tool 的 reason_code 必须是 need_balance')

    def _validate_leave_request(self) -> None:
        if not isinstance(self.arguments, dict) or not self.arguments:
            raise PlannerDecisionError('action=tool 必须提供非空 arguments')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _LEAVE_REQUEST_ARG_KEYS:
            raise PlannerDecisionError(
                f'leave_request_tool 的 arguments 只允许字段 {sorted(_LEAVE_REQUEST_ARG_KEYS)}'
            )
        limit = self.arguments.get('limit')
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise PlannerDecisionError('leave_request_tool 的 limit 必须是整数')
        if not (LEAVE_REQUEST_MIN_LIMIT <= limit <= LEAVE_REQUEST_MAX_LIMIT):
            raise PlannerDecisionError(
                f'leave_request_tool 的 limit 必须在 '
                f'{LEAVE_REQUEST_MIN_LIMIT}..{LEAVE_REQUEST_MAX_LIMIT} 之间'
            )
        if self.reason_code != 'need_leave_history':
            raise PlannerDecisionError(
                'leave_request_tool 的 reason_code 必须是 need_leave_history'
            )

    def _validate_leave_proposal(self) -> None:
        # leave_proposal_tool 不接受任何 LLM 入参:日期 / 原因 / 半天的解析由
        # Executor 基于用户原始问题交给受控业务动作链路完成,模型不能夹带这些
        # 字段绕过校验或注入业务身份 / 系统控制信息。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _LEAVE_PROPOSAL_ARG_KEYS:
            raise PlannerDecisionError(
                'leave_proposal_tool 不接受任何 LLM 参数;'
                '日期 / 原因 / 半天等由程序层基于用户原始问题解析'
            )
        if self.reason_code != 'need_proposal':
            raise PlannerDecisionError(
                'leave_proposal_tool 的 reason_code 必须是 need_proposal'
            )

    # ── P2-A Expense Workflow V1: 4 新 Tool 校验 ──────────────────

    def _validate_travel_record(self) -> None:
        # travel_record_tool 不接受 LLM 入参:employee_id / limit 等均由
        # Executor 从 AgentState 注入。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _TRAVEL_RECORD_ARG_KEYS:
            raise PlannerDecisionError(
                'travel_record_tool 不接受任何 LLM 参数；employee_id / limit 由程序层注入'
            )
        if self.reason_code != 'need_travel_history':
            raise PlannerDecisionError(
                'travel_record_tool 的 reason_code 必须是 need_travel_history'
            )

    def _validate_invoice_verify(self) -> None:
        # invoice_verify_tool 强制 identity_required=true（V2 §十一）：
        # LLM 只能传 invoice_id；employee_id / trace_id 由 Executor 注入。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _INVOICE_VERIFY_ARG_KEYS:
            raise PlannerDecisionError(
                f'invoice_verify_tool 的 arguments 只允许字段 '
                f'{sorted(_INVOICE_VERIFY_ARG_KEYS)}；employee_id 不得由 LLM 提供'
            )
        invoice_id = self.arguments.get('invoice_id')
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise PlannerDecisionError('invoice_verify_tool 必须提供非空 invoice_id 参数')
        if self.reason_code != 'need_invoice_verify':
            raise PlannerDecisionError(
                'invoice_verify_tool 的 reason_code 必须是 need_invoice_verify'
            )

    def _validate_expense_proposal(self) -> None:
        # expense_proposal_tool 的 arguments 不接受 LLM 入参：trip_id / 费用明细 /
        # cost_center 等由 Executor 注入的 ExpenseProposalContext 携带（从
        # tool_history 中已成功完成的 travel/invoice/rag observation 抽取）；
        # expense_reason 是独立的 Planner 语义字段，不进入 arguments。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _EXPENSE_PROPOSAL_ARG_KEYS:
            raise PlannerDecisionError(
                'expense_proposal_tool 不接受任何 LLM 参数；'
                '业务事实由 Executor 从 tool_history 注入，expense_reason 由决策字段单独传递'
            )
        if self.reason_code != 'need_expense_proposal':
            raise PlannerDecisionError(
                'expense_proposal_tool 的 reason_code 必须是 need_expense_proposal'
            )

    def _validate_expense_status(self) -> None:
        # expense_status_tool：employee_id 由 Executor 注入，LLM 可选传 expense_id。
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        arg_keys = set(self.arguments)
        if not arg_keys.issubset(_EXPENSE_STATUS_ARG_KEYS):
            raise PlannerDecisionError(
                f'expense_status_tool 的 arguments 只允许字段 '
                f'{sorted(_EXPENSE_STATUS_ARG_KEYS)}'
            )
        if 'expense_id' in self.arguments:
            eid = self.arguments['expense_id']
            if not isinstance(eid, str) or not eid.strip():
                raise PlannerDecisionError(
                    'expense_status_tool 的 expense_id 必须是非空字符串'
                )
        if self.reason_code != 'need_expense_status':
            raise PlannerDecisionError(
                'expense_status_tool 的 reason_code 必须是 need_expense_status'
            )

    def _validate_purchase_budget(self) -> None:
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _PURCHASE_BUDGET_ARG_KEYS:
            raise PlannerDecisionError('purchase_budget_tool 不接受任何 LLM 参数')
        if self.reason_code != 'need_purchase_budget':
            raise PlannerDecisionError(
                'purchase_budget_tool 的 reason_code 必须是 need_purchase_budget'
            )

    def _validate_purchase_policy(self) -> None:
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _PURCHASE_POLICY_ARG_KEYS:
            raise PlannerDecisionError('purchase_policy_tool 不接受任何 LLM 参数')
        if self.reason_code != 'need_purchase_policy':
            raise PlannerDecisionError(
                'purchase_policy_tool 的 reason_code 必须是 need_purchase_policy'
            )

    def _validate_purchase_proposal(self) -> None:
        if not isinstance(self.arguments, dict):
            raise PlannerDecisionError('action=tool 必须提供 arguments(可为空 dict)')
        if self.answer is not None:
            raise PlannerDecisionError('action=tool 不得携带 answer')
        if set(self.arguments) != _PURCHASE_PROPOSAL_ARG_KEYS:
            raise PlannerDecisionError('purchase_proposal_tool 不接受任何 LLM 参数')
        if self.reason_code != 'need_purchase_proposal':
            raise PlannerDecisionError(
                'purchase_proposal_tool 的 reason_code 必须是 need_purchase_proposal'
            )
