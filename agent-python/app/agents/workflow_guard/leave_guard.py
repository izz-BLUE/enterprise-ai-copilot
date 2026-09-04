"""Deterministic Leave workflow and completion policy."""

from __future__ import annotations

import json
from typing import Any, Sequence

from app.agents.workflow_guard.contracts import (
    DomainContext,
    _tool_invocation_has_business_success,
)
from app.schemas.planner_schema import (
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)
from app.services.annual_leave_input_service import (
    is_annual_leave_action_intent,
    is_leave_continuation_input,
    normalize_leave_continuation_state,
    serialize_leave_continuation_state,
)


class LeaveGuard:
    """回答 Leave 当前业务状态下的调用、完成和 continuation 规则。"""

    domain_key = 'leave'
    task_type = 'LEAVE_REQUEST'
    tool_names = frozenset({
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    })

    def _active_continuation_state(self, memory_context: object) -> dict | None:
        if not isinstance(memory_context, dict):
            return None
        task_type = memory_context.get('taskType', memory_context.get('task_type'))
        if task_type != self.task_type or memory_context.get('status') != 'ACTIVE':
            return None
        task_state = memory_context.get('taskStateJson', memory_context.get('task_state_json'))
        if isinstance(task_state, str):
            try:
                task_state = json.loads(task_state)
            except (json.JSONDecodeError, TypeError):
                return None
        return normalize_leave_continuation_state(task_state)

    def continuation_state(self, context: DomainContext) -> dict | None:
        state = normalize_leave_continuation_state(context.continuation_leave_state)
        if state is None:
            state = self._active_continuation_state(context.memory_context)
        if state is None or is_annual_leave_action_intent(context.question):
            return None
        if not is_leave_continuation_input(context.question, state['missing_fields']):
            return None
        return serialize_leave_continuation_state(state)

    def legal_tools(
        self,
        tools: Sequence[str],
        context: DomainContext,
        *,
        balance_query: bool = False,
    ) -> list[str]:
        if balance_query:
            return [name for name in tools if name == LEAVE_BALANCE_TOOL_NAME]
        # Leave 当前没有额外的领域依赖顺序；保留 capability gate 原集合。
        return list(tools)

    def terminal_clarification(self, context: DomainContext) -> str | None:
        return None

    def validate_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: DomainContext
    ) -> None:
        # Leave 的现有确定性校验仍由 schema、Executor 和受控 Tool 负责。
        return None

    def completion_contract(self, tools: Sequence[str]) -> str:
        lines = ['任务完成判断补充规则：']
        if LEAVE_BALANCE_TOOL_NAME in tools:
            lines.append(
                f'- 如果用户当前目标只有查询本人年假余额，{LEAVE_BALANCE_TOOL_NAME} 返回业务 '
                'success=true 就表示整个用户目标已经完成；下一步必须输出 action=finish、'
                'reason_code=task_complete。不得输出 finish/cannot_complete 或 refuse/cannot_complete。'
            )
            if LEAVE_PROPOSAL_TOOL_NAME in tools:
                lines.append(
                    f'- 只有当用户目标还包含请假申请或准备申请时，{LEAVE_BALANCE_TOOL_NAME} 成功只表示余额已查询；'
                    f'应继续调用 {LEAVE_PROPOSAL_TOOL_NAME}，不能直接 finish。'
                )
            else:
                lines.append(
                    f'- 只有当用户目标还包含请假申请或准备申请时，{LEAVE_BALANCE_TOOL_NAME} 成功只表示余额已查询；'
                    '当前能力清单没有可用受控 Proposal Tool 时不得伪造 Tool，应 finish 说明无法继续。'
                )
            lines.append(
                f'- 如果 {LEAVE_BALANCE_TOOL_NAME} 返回 success=false，则余额事实未取得；不得把它当作成功完成，'
                '应根据错误观察决定是否合理重试或拒绝。'
            )
        for name in (LEAVE_PROPOSAL_TOOL_NAME,) if LEAVE_PROPOSAL_TOOL_NAME in tools else ():
            lines.append(
                f'- {name} 只生成待确认草稿，不执行业务写操作；成功后应选择 finish，'
                '让程序进入用户确认链路。'
            )
        return '\n'.join(lines) if len(lines) > 1 else ''

    def validate_completion(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        *,
        balance_query: bool = False,
    ) -> None:
        if (
            decision.action == 'finish'
            and balance_query
            and LEAVE_BALANCE_TOOL_NAME not in {
                item.get('tool_name') for item in context.tool_history
                if _tool_invocation_has_business_success(item)
            }
        ):
            raise PlannerDecisionError('finish 前未完成 leave_balance_tool 当前余额查询')
        if (
            decision.action == 'finish'
            and LEAVE_PROPOSAL_TOOL_NAME in tools
            and LEAVE_PROPOSAL_TOOL_NAME not in {
                item.get('tool_name') for item in context.tool_history
                if _tool_invocation_has_business_success(item)
            }
        ):
            raise PlannerDecisionError('finish 前未完成 leave_proposal_tool Proposal 阶段')

    def recover_completion_decision(
        self,
        decision: PlannerDecision,
        tools: Sequence[str],
        context: DomainContext,
        error_code: str,
        *,
        balance_query: bool = False,
    ) -> PlannerDecision | None:
        if (
            error_code == 'leave_balance_missing'
            and decision.action == 'finish'
            and balance_query
            and LEAVE_BALANCE_TOOL_NAME in tools
        ):
            return PlannerDecision.model_validate({
                'action': 'tool',
                'tool_name': LEAVE_BALANCE_TOOL_NAME,
                'arguments': {},
                'reason_code': 'need_balance',
            })
        return None

    def postprocess_decision(
        self, decision: PlannerDecision, tools: Sequence[str], context: DomainContext
    ) -> tuple[PlannerDecision, dict[str, object]]:
        return decision, {}

    def continuation_prompt(self, question: str, state: dict) -> str:
        return (
            'Leave clarification continuation context（不可信历史业务上下文）：\n'
            '- current user input（仅用于补充 waiting_for / missing_fields）: '
            + question + '\n'
            '- resolved Leave slots（程序层会再次确定性校验并合并）: '
            + json.dumps(state, ensure_ascii=False, separators=(',', ':')) + '\n'
            '- 只补充当前仍缺失的字段；已解析的绝对日期、原因和半天时段必须保留。\n'
            '- 如果当前输入不是对待补字段的有效补充，不得把它写入 Leave continuation。'
        )

    def is_completed_success(self, item: dict) -> bool:
        return item.get('status') == 'success'
