"""
planner_schema.py —— Planner 决策数据结构与确定性校验

Planner 只拥有"规划权":模型输出严格结构化的"下一步决策",
程序层负责字段一致性校验与权限校验。
系统字段(trace_id / original_question / 权限字段 / 业务身份信息)
不属于模型可控范围,由程序层在 Tool 执行时注入。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Action = Literal['tool', 'finish', 'refuse']
ToolName = Literal[
    'rag_answer_tool',
    'eval_report_tool',
    'leave_balance_tool',
    'leave_request_tool',
]
ReasonCode = Literal[
    'need_knowledge',
    'need_eval',
    'need_balance',
    'need_leave_history',
    'task_complete',
    'not_allowed',
    'cannot_complete',
]

RAG_TOOL_NAME = 'rag_answer_tool'
EVAL_TOOL_NAME = 'eval_report_tool'
LEAVE_BALANCE_TOOL_NAME = 'leave_balance_tool'
LEAVE_REQUEST_TOOL_NAME = 'leave_request_tool'
VALID_REPORT_TYPES = ('retrieval', 'generation', 'all')
LEAVE_REQUEST_MIN_LIMIT = 1
LEAVE_REQUEST_MAX_LIMIT = 50

# Tool arguments 严格白名单:模型只能生成业务参数,不能夹带系统控制字段
_RAG_TOOL_ARG_KEYS = frozenset({'question'})
_EVAL_TOOL_ARG_KEYS = frozenset({'report_type'})
_LEAVE_BALANCE_ARG_KEYS = frozenset()  # 无 LLM 入参;身份全部由 Executor 注入
_LEAVE_REQUEST_ARG_KEYS = frozenset({'limit'})


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