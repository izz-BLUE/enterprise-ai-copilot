"""
tool_executor_node.py —— Tool 执行节点

只执行 PlannerDecision.action == "tool" 的决策；发起执行前再次完成
结构、权限、Tool 调用预算与连续重复调用校验。
真正发起 Tool 执行前 tool_call_count += 1（成功、超时、异常都消耗
一次调用预算）。Tool 结果与异常均转为结构化 Observation 交回 Planner
决定下一步，不让整个 Agent 崩溃。
"""

import json

from pydantic import ValidationError

from app.core.config import logger
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    RAG_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)
from app.tools.enterprise_tools import (
    leave_balance_tool,
    leave_proposal_tool,
    leave_request_tool,
)
from app.tools.rag_tools import eval_report_tool, rag_answer_tool

# 仅供只读企业 Tool 使用;Planner arguments 不得出现这些 key
_LEAVE_SYSTEM_ARG_KEYS = frozenset({'employee_id', 'trace_id'})

# leave_proposal_tool 的系统字段与业务字段:全部由 Executor 从 AgentState 注入,
# 模型 arguments 中不得夹带任何一项(业务参数由受控链路基于原始问题解析)
_PROPOSAL_SYSTEM_ARG_KEYS = frozenset({
    'employee_id', 'trace_id', 'business_date',
    'start_date', 'end_date', 'reason', 'half_day',
})

# 这三个 Tool 都必须绑定 Java 已认证请求中的员工身份；缺失时在真正调用
# Tool 之前阻断。只读 Tool 的 Java URL / internal token 仍由下游 Tool / Client 校验。
_EMPLOYEE_REQUIRED_TOOLS = frozenset({
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
})

# 单次任务允许的最大 Tool 执行次数（真正发起执行的次数，成功/失败都计数）。
# 小于 MAX_PLANNER_STEPS(5)，使 Tool 预算成为独立防线：连续请求 Tool 时
# 由 Executor 先拦截，而不是永远被 Planner 步骤预算遮蔽。
MAX_TOOL_CALLS = 3

# 异常转 Observation 的稳定错误结构：完整异常只进内部日志，不给 Planner
_ERROR_MESSAGES = {
    'tool_timeout': '工具执行超时，已终止本次调用。',
    'tool_execution_failed': '工具执行失败，已终止本次调用。',
}


def _error_code(exc: Exception) -> str:
    """异常分类：超时类异常映射为 tool_timeout，其余为 tool_execution_failed。"""
    if isinstance(exc, TimeoutError) or 'timeout' in type(exc).__name__.lower():
        return 'tool_timeout'
    return 'tool_execution_failed'


def _get_tool(tool_name: str):
    """每次执行时从模块命名空间解析工具(不缓存快照,保证测试 patch 生效)。"""
    if tool_name == RAG_TOOL_NAME:
        return rag_answer_tool
    if tool_name == EVAL_TOOL_NAME:
        return eval_report_tool
    if tool_name == LEAVE_BALANCE_TOOL_NAME:
        return leave_balance_tool
    if tool_name == LEAVE_REQUEST_TOOL_NAME:
        return leave_request_tool
    if tool_name == LEAVE_PROPOSAL_TOOL_NAME:
        return leave_proposal_tool
    raise ValueError(f'Unknown tool: {tool_name}')


def _blocked(state: dict, stop_reason: str, message: str,
             tool_name=None, arguments=None, category: str = '') -> dict:
    """执行前被阻止：未真正发起 Tool 执行，不消耗 tool_call_count。

    category 是程序层对本次终止语义的预先归类（access_control / business_action），
    最终响应契约收敛时优先保留，避免仅靠 reason_code 区分 Eval 与受控业务动作。
    """
    observation = json.dumps({
        'status': 'blocked',
        'reason': stop_reason,
        'message': message,
        'tool_name': tool_name,
    }, ensure_ascii=False)
    tool_history = list(state.get('tool_history', []))
    tool_history.append({
        'tool_name': tool_name,
        'arguments': arguments,
        'status': 'blocked',
        'observation': observation,
    })
    updates: dict = {
        'tool_call_count': state.get('tool_call_count', 0),
        'tool_history': tool_history,
        'observation': observation,
        'stop_reason': stop_reason,
    }
    if category:
        updates['category'] = category
    return updates


def _already_completed(decision: PlannerDecision, tool_history: list) -> bool:
    """成功签名去重：历史中存在相同 tool + 相同 arguments 且 status=success
    时阻止再次执行；error / timeout / blocked 历史不阻止，允许合理重试。"""
    for item in tool_history:
        if (
            item.get('tool_name') == decision.tool_name
            and item.get('arguments') == decision.arguments
            and item.get('status') == 'success'
        ):
            return True
    return False


def tool_executor_node(state: dict) -> dict:
    """Tool 执行节点。

    校验顺序：结构（tool_name/arguments）→ employee_id → 权限 → Tool 调用预算 →
    成功签名去重 → 计数并真正执行。任何执行前拦截都不计数。
    返回更新 state 的字段：
      tool_call_count — 更新后的 Tool 调用次数
      tool_history    — 追加本条调用记录（success/error/blocked）
      observation     — 结构化观察（Tool 原始结果、错误或阻止原因）
      stop_reason     — tool_executed | invalid_decision
                        | not_allowed
                        | tool_call_budget_exhausted | already_completed
    """
    trace_id = state.get('trace_id', '')
    decision_raw = state.get('planner_decision')
    tool_call_count = state.get('tool_call_count', 0)
    tool_history = list(state.get('tool_history', []))

    # 1. 结构再校验：仅处理 action=tool 的合法决策（不信赖 Planner 已校验）
    if not isinstance(decision_raw, dict):
        return _blocked(state, 'invalid_decision', '缺少合法的 Planner 决策，已拒绝执行。')
    try:
        decision = PlannerDecision.model_validate(decision_raw)
        decision.validate_decision()
    except (ValidationError, PlannerDecisionError) as exc:
        logger.warning('[%s] tool_executor 决策非法: %s', trace_id, exc)
        return _blocked(state, 'invalid_decision', f'Tool 决策非法，已拒绝执行：{exc}')
    if decision.action != 'tool':
        return _blocked(state, 'invalid_decision', 'Tool Executor 仅处理 action=tool 的决策。')

    # 2. 身份前置校验（即使 Capability Gate / Planner 已校验，Executor 独立确认）
    employee_id = (state.get('employee_id') or '').strip()
    if decision.tool_name in _EMPLOYEE_REQUIRED_TOOLS and not employee_id:
        category = (
            'business_action'
            if decision.tool_name == LEAVE_PROPOSAL_TOOL_NAME
            else 'access_control'
        )
        logger.warning(
            '[%s] tool_executor 拒绝无 employee_id 的 Tool=%s',
            trace_id, decision.tool_name,
        )
        return _blocked(
            state,
            'not_allowed',
            '当前请求缺少员工身份，已拒绝执行。',
            tool_name=decision.tool_name,
            arguments=decision.arguments,
            category=category,
        )

    # 3. 权限再校验（即使 Planner 已校验，Executor 独立确认）
    if decision.tool_name == EVAL_TOOL_NAME and not state.get('allow_eval', False):
        logger.warning('[%s] tool_executor 越权执行 %s 被拒绝', trace_id, EVAL_TOOL_NAME)
        return _blocked(state, 'not_allowed', 'eval_report_tool 需要管理员权限，已拒绝执行。',
                        tool_name=decision.tool_name, arguments=decision.arguments,
                        category='access_control')
    if decision.tool_name == LEAVE_PROPOSAL_TOOL_NAME:
        if not state.get('allow_business_actions', False):
            logger.warning('[%s] tool_executor 越权执行 %s 被拒绝', trace_id, LEAVE_PROPOSAL_TOOL_NAME)
            return _blocked(state, 'not_allowed',
                            '业务动作功能未启用，或当前请求无执行权限。',
                            tool_name=decision.tool_name, arguments=decision.arguments,
                            category='business_action')
        if state.get('business_date') is None:
            logger.warning('[%s] tool_executor 拒绝执行 %s：无业务日期', trace_id, LEAVE_PROPOSAL_TOOL_NAME)
            return _blocked(state, 'not_allowed', '当前业务日期不可用。',
                            tool_name=decision.tool_name, arguments=decision.arguments,
                            category='business_action')

    # 4. Tool 调用预算（基于实际发起执行的次数）
    if tool_call_count >= MAX_TOOL_CALLS:
        return _blocked(state, 'tool_call_budget_exhausted',
                        'Tool 调用预算已耗尽，无法继续执行工具。',
                        tool_name=decision.tool_name, arguments=decision.arguments)

    # 5. 成功签名去重：相同 tool + 相同 arguments 且已成功完成 → 阻止；
    #    error / timeout 历史不阻止，允许合理重试
    if _already_completed(decision, tool_history):
        return _blocked(state, 'already_completed',
                        '该 Tool 调用已成功完成（相同工具与相同参数），不重复执行。',
                        tool_name=decision.tool_name, arguments=decision.arguments)

    # 6. 执行前计数：真正发起执行即消耗一次调用预算（成功/超时/异常都计数）
    tool_call_count += 1
    try:
        args = dict(decision.arguments)
        if decision.tool_name == RAG_TOOL_NAME:
            # 系统字段由 Executor 注入，不经过模型
            args['original_question'] = state.get('question', '')
            args['trace_id'] = trace_id
        elif decision.tool_name in (LEAVE_BALANCE_TOOL_NAME, LEAVE_REQUEST_TOOL_NAME):
            # 企业 Tool P0：身份由 Java 注入到 AgentState.employee_id，
            # Executor 转发给 Tool；模型不得在 arguments 中夹带这些字段。
            leaked = set(decision.arguments or {}).intersection(_LEAVE_SYSTEM_ARG_KEYS)
            if leaked:
                raise PlannerDecisionError(
                    f'{decision.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
                )
            args['employee_id'] = employee_id
            args['trace_id'] = trace_id
        elif decision.tool_name == LEAVE_PROPOSAL_TOOL_NAME:
            # Composite Enterprise Task P0：原始问题 / business_date / trace_id
            # 由 Executor 注入；模型不得夹带任何系统或业务字段（日期 / 原因等
            # 由受控链路基于原始问题确定性解析）。
            leaked = set(decision.arguments or {}).intersection(_PROPOSAL_SYSTEM_ARG_KEYS)
            if leaked:
                raise PlannerDecisionError(
                    f'{decision.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
                )
            business_date = state.get('business_date')
            args['question'] = state.get('question', '')
            args['business_date'] = business_date.isoformat() if business_date else ''
            args['trace_id'] = trace_id
        result = _get_tool(decision.tool_name).invoke(args)
        observation = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        status = 'success'
    except Exception as exc:
        # 完整异常只记录内部日志并关联 trace_id，绝不外泄给 Planner
        logger.warning('[%s] %s 执行失败，完整异常仅记录内部日志: %s',
                       trace_id, decision.tool_name, exc, exc_info=True)
        status = 'error'
        error_code = _error_code(exc)
        observation = json.dumps({
            'tool_name': decision.tool_name,
            'status': 'error',
            'error_code': error_code,
            'message': _ERROR_MESSAGES[error_code],
        }, ensure_ascii=False)

    tool_history.append({
        'tool_name': decision.tool_name,
        'arguments': decision.arguments,
        'status': status,
        'observation': observation,
    })
    logger.info('[%s] tool 执行 tool_name=%s status=%s tool_call_count=%d',
                trace_id, decision.tool_name, status, tool_call_count)
    updates: dict = {
        'tool_call_count': tool_call_count,
        'tool_history': tool_history,
        'observation': observation,
        'stop_reason': 'tool_executed',
    }
    # Composite Enterprise Task P0：leave_proposal_tool 的 proposal / clarification
    # 结果同步回 AgentState，供最终响应与后续链路使用。
    if decision.tool_name == LEAVE_PROPOSAL_TOOL_NAME:
        try:
            parsed = json.loads(observation) if isinstance(observation, str) else {}
        except json.JSONDecodeError:
            parsed = {}
        updates['action_proposal'] = parsed.get('action_proposal')
        updates['missing_fields'] = parsed.get('missing_fields', [])
    return updates
