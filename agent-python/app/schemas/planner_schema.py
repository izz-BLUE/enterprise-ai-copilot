"""
planner_schema.py —— Planner 决策数据结构与确定性校验

Planner 只拥有"规划权"：模型输出严格结构化的"下一步决策"，
程序层负责字段一致性校验与权限校验。
系统字段（trace_id / original_question / 权限字段 / 业务身份信息）
不属于模型可控范围，由程序层在 Tool 执行时注入。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Action = Literal['tool', 'finish', 'refuse']
ToolName = Literal['rag_answer_tool', 'eval_report_tool']
ReasonCode = Literal[
    'need_knowledge', 'need_eval', 'task_complete', 'not_allowed', 'cannot_complete'
]

RAG_TOOL_NAME = 'rag_answer_tool'
EVAL_TOOL_NAME = 'eval_report_tool'
VALID_REPORT_TYPES = ('retrieval', 'generation', 'all')

# Tool arguments 严格白名单：模型只能生成业务参数，不能夹带系统控制字段
_RAG_TOOL_ARG_KEYS = frozenset({'question'})
_EVAL_TOOL_ARG_KEYS = frozenset({'report_type'})


class PlannerDecisionError(ValueError):
    """PlannerDecision 字段一致性校验失败。"""


class PlannerDecision(BaseModel):
    """严格的"下一步决策"结构。

    约束（由 validate_decision 确定性校验，非法结构抛错而非静默修复）：
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
        """确定性字段一致性校验；非法结构抛 PlannerDecisionError，不静默修复。"""
        if self.action == 'tool':
            if self.tool_name is None:
                raise PlannerDecisionError('action=tool 必须提供 tool_name')
            if not isinstance(self.arguments, dict) or not self.arguments:
                raise PlannerDecisionError('action=tool 必须提供非空 arguments')
            if self.tool_name == RAG_TOOL_NAME:
                if set(self.arguments) != _RAG_TOOL_ARG_KEYS:
                    raise PlannerDecisionError(
                        f'rag_answer_tool 的 arguments 只允许字段 {sorted(_RAG_TOOL_ARG_KEYS)}'
                    )
                question = self.arguments['question']
                if not isinstance(question, str) or not question.strip():
                    raise PlannerDecisionError('rag_answer_tool 必须提供非空 question 参数')
            else:
                if set(self.arguments) != _EVAL_TOOL_ARG_KEYS:
                    raise PlannerDecisionError(
                        f'eval_report_tool 的 arguments 只允许字段 {sorted(_EVAL_TOOL_ARG_KEYS)}'
                    )
                if self.arguments['report_type'] not in VALID_REPORT_TYPES:
                    raise PlannerDecisionError(
                        f'eval_report_tool 的 report_type 必须是 {VALID_REPORT_TYPES} 之一'
                    )
        else:
            if self.tool_name is not None:
                raise PlannerDecisionError(f'action={self.action} 不得携带 tool_name')
            if not self.answer or not self.answer.strip():
                raise PlannerDecisionError(f'action={self.action} 必须提供非空 answer')
        return self
