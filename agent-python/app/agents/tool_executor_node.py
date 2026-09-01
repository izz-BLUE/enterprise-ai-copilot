"""
tool_executor_node.py —— Tool 执行节点

只执行 PlannerDecision.action == "tool" 的决策；发起执行前再次完成
结构、权限、Tool 调用预算与连续重复调用校验。
真正发起 Tool 执行前 tool_call_count += 1（成功、超时、异常都消耗
一次调用预算）。Tool 结果与异常均转为结构化 Observation 交回 Planner
决定下一步，不让整个 Agent 崩溃。

P2-A Expense Workflow V1：
Tool 注册表（ToolSpec + _TOOL_REGISTRY）替换原 _get_tool if/elif。
新增 Tool 时只需在 _TOOL_REGISTRY 添加 ToolSpec，不再扩展 executor 分支。
"""

import json
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from typing import Any, Callable

from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.agents.domain_provider_registry import (
    DOMAIN_PROVIDER_REGISTRY,
    DomainContext,
    DomainProviderAmbiguityError,
    DomainToolCallRejected,
    build_expense_proposal_context,
)
from app.agents.runtime_context import AgentRuntimeContext
from app.core.config import logger
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
    PURCHASE_PROPOSAL_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)
from app.tools.enterprise_tools import (
    expense_proposal_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    expense_status_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    invoice_verify_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    leave_balance_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    leave_proposal_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    leave_request_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    purchase_budget_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    purchase_policy_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    purchase_proposal_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
    travel_record_tool,  # noqa: F401 - registry 通过 globals() 解析工具。
)
from app.tools.rag_tools import eval_report_tool, rag_answer_tool  # noqa: F401 - 见上方 registry 查找。

# 单次任务允许的最大 Tool 执行次数（真正发起执行的次数，成功/失败都计数）。
# P2-A Expense Workflow V1: 提升到 5 以容纳 travel/rag/invoice/proposal/status
# 五步目标链；仍小于 MAX_PLANNER_STEPS(6)，Tool 预算保持独立防线。
MAX_TOOL_CALLS = 5

# 异常转 Observation 的稳定错误结构：完整异常只进内部日志，不给 Planner
_ERROR_MESSAGES = {
    'tool_timeout': '工具执行超时，已终止本次调用。',
    'tool_execution_failed': '工具执行失败，已终止本次调用。',
}

# leave_proposal_tool 输出 action_proposal 时,Tool 出口把 Python date 序列化为
# ISO 字符串以越过 HTTP / Tool 输出边界。Executor 解析回 dict 后,在此还原
# 关键日期字段回 Python date 对象,使下游 Pydantic strict schema (AnnualLeaveActionProposal,
# strict=True) 接受 —— 这与"内部 Pydantic 对象之间传递保持 date 类型"对齐。
_LEAVE_PROPOSAL_DATE_FIELDS = ('start_date', 'end_date')


def _restore_iso_date_fields(payload: dict | None) -> dict | None:
    """只在 leave_proposal_tool 的 action_proposal dict 上还原 ISO 日期字段。

    - 仅识别合法 ISO-8601 (YYYY-MM-DD) 字符串;其它字符串值保持原状(不抛错,
      保留后续 schema 校验作为最终防线)。
    - 不修改其他字段;不修改 missing_fields(Literal 集合,本来就是 list[str])。
    - 缺失或非字典值原样返回。
    """
    if not isinstance(payload, dict):
        return payload
    for key in _LEAVE_PROPOSAL_DATE_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and len(value) == 10:
            try:
                payload[key] = date.fromisoformat(value)
            except ValueError:
                # 非合法 ISO 日期:不抛错,留给 schema 校验拒绝。
                pass
    return payload


def _error_code(exc: Exception) -> str:
    """异常分类：超时类异常映射为 tool_timeout，其余为 tool_execution_failed。"""
    if isinstance(exc, TimeoutError) or 'timeout' in type(exc).__name__.lower():
        return 'tool_timeout'
    return 'tool_execution_failed'


# ---------------------------------------------------------------------------
# Tool 注册表
# ---------------------------------------------------------------------------
# ToolSpec 把"身份要求 / 系统字段集 / 执行前注入 / Proposal 解析"从 executor
# 主流程里抽出来，每加一个 Tool 只在 _TOOL_REGISTRY 注册一行，executor
# 主流程（结构/权限/budget/dedup/history/observation）保持不变。
#
# 字段语义：
#   name                       - Tool 字符串名，与 planner_schema.ToolName Literal
#                                同源；用于 _TOOL_REGISTRY 索引与日志。
#   executable_ref             - 模块全局变量名（字符串），executor 每次执行时
#                                通过 globals()[ref] 解析，使测试 patch
#                                'app.agents.tool_executor_node.rag_answer_tool'
#                                等模块 attr 后立即生效。
#   identity_required          - True 表示必须在 executor 调用前由当前 Runtime
#                                Context 注入 employee_id；缺失则 executor 阻断。
#   system_arg_keys            - 模型在 arguments 中**禁止**夹带的系统/业务字段
#                                集合；命中即抛 PlannerDecisionError 阻断。
#                                LLM 业务参数由对应工具的 arg_contract 单独约束。
#   no_employee_blocked_category - 缺 employee_id 时归类 category（business_action
#                                | access_control），仅在 identity_required=True
#                                时使用。
#   pre_inject                 - 可选钩子：executor 在 .invoke 前调用，签名
#                                (decision_args, ctx) -> dict；返回合并到调用参数。
#                                不需要注入则 None。
#   proposal_post              - 可选钩子：executor 解析 observation 后调用，
#                                返回要写回 AgentState 的字段 dict（如
#                                action_proposal / missing_fields）。不是
#                                proposal 风格 Tool 则 None。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _ExecutorContext:
    tool_name: str
    employee_id: str
    trace_id: str
    question: str
    business_date: Any  # date | None
    expense_reason: str | None = None
    # ACTIVE Expense continuation 的 Q1 仅供 expense_proposal_tool 的
    # 差旅/发票确定性解析；普通 Tool 仍只接收当前请求 question。
    expense_request_question: str | None = None
    purchase_item: str | None = None
    purchase_budget: Any = None
    purchase_justification: str | None = None
    tool_history: list[dict] = field(default_factory=list)  # 用于构造 ExpenseProposalContext


@dataclass(frozen=True)
class ToolSpec:
    name: str
    executable_ref: str  # 模块全局名称；调用时通过 globals() 解析
    identity_required: bool = False
    system_arg_keys: frozenset = field(default_factory=frozenset)
    no_employee_blocked_category: str = 'access_control'
    pre_inject: Callable[[dict, _ExecutorContext], dict] | None = None
    proposal_post: Callable[[dict, str], dict] | None = None
    # Crash Resume 默认拒绝 Tool 重放；只有完成副作用审计的 Tool 才显式打开。
    resume_safe: bool = False


# --- Pre-inject hooks（预注入钩子） ----------------------------------------

def _inject_rag(args: dict, ctx: _ExecutorContext) -> dict:
    # 系统字段由 Executor 注入，不经过模型
    merged = dict(args)
    merged['original_question'] = ctx.question
    merged['trace_id'] = ctx.trace_id
    return merged


def _inject_leave_read(args: dict, ctx: _ExecutorContext) -> dict:
    # 企业 Tool P0：身份由 Java 注入当前 Runtime Context，Executor 转发给
    # Tool；模型不得在 arguments 中夹带这些字段。
    merged = dict(args)
    leaked = set(args or {}).intersection(_LEAVE_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['employee_id'] = ctx.employee_id
    merged['trace_id'] = ctx.trace_id
    return merged


def _inject_leave_proposal(args: dict, ctx: _ExecutorContext) -> dict:
    # Composite Enterprise Task P0：原始问题 / business_date / trace_id
    # 由 Executor 注入；模型不得夹带任何系统或业务字段（日期 / 原因等
    # 由受控链路基于原始问题确定性解析）。
    merged = dict(args)
    leaked = set(args or {}).intersection(_PROPOSAL_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['question'] = ctx.question
    merged['business_date'] = ctx.business_date.isoformat() if ctx.business_date else ''
    merged['trace_id'] = ctx.trace_id
    return merged


# ── P2-A Expense Workflow V1：共享 MCP read 预注入钩子 ─────────────────────
# travel_record_tool 与 invoice_verify_tool 都要求 identity_required=true
# 且 executor 注入 employee_id / trace_id；LLM 不得传 employee_id（V2 §十一）。
# limit 不属于 LLM 必填字段，Executor 注入默认值。
_OAMCP_READ_SYSTEM_ARG_KEYS = frozenset({'employee_id', 'trace_id'})


def _inject_oamcp_read(args: dict, ctx: _ExecutorContext) -> dict:
    """Enterprise OA MCP 只读 Tool 的 pre-inject 钩子。

    - 注入 employee_id / trace_id（trusted system field）
    - 拒绝 LLM 在 arguments 中夹带 employee_id / trace_id
    - 对 travel_record_tool：注入默认 limit=10
    """
    merged = dict(args or {})
    leaked = set(args or {}).intersection(_OAMCP_READ_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['employee_id'] = ctx.employee_id
    merged['trace_id'] = ctx.trace_id
    if ctx.tool_name == TRAVEL_RECORD_TOOL_NAME and 'limit' not in merged:
        merged['limit'] = 10
    return merged


# ── P2-A Expense Workflow V1：expense_proposal_tool 的 context 注入 ────────
# 追加约束 §1/§2：ExpenseProposalContext 必须由程序层从当前请求成功
# tool_history 确定性构造（不允许把 raw tool_history 交给 LLM 解析）。
# 从 tool_history 中抽取：
#   - travel_record_tool success → travel_record 列表（trip 记录）
#   - invoice_verify_tool success → invoices 列表（验真结果）
#   - rag_answer_tool success    → policy_context（政策知识解释）
# Tool 内部（在 Python 侧）禁止重新调用 MCP / Java / RAG（V2 §十三）。
_EXPENSE_CTCX_SYSTEM_ARG_KEYS = frozenset({
    'employee_id', 'trace_id', 'business_date', 'context',
})


# ExpenseProposalContext 构造和 invoice scope 判断均由 ExpenseProvider 负责。
_build_expense_proposal_context = build_expense_proposal_context

def _inject_expense_proposal(args: dict, ctx: _ExecutorContext) -> dict:
    """expense_proposal_tool 的 pre-inject 钩子。

    - 注入 question / business_date / trace_id / context（ExpenseProposalContext）
      以及独立的 Planner expense_reason
    - 拒绝 LLM 在 arguments 中夹带任何系统字段（V2 §十五：禁止 trusted identity）
    """
    merged = dict(args or {})
    leaked = set(args or {}).intersection(_EXPENSE_CTCX_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['question'] = ctx.expense_request_question or ctx.question
    merged['business_date'] = ctx.business_date.isoformat() if ctx.business_date else ''
    merged['trace_id'] = ctx.trace_id
    merged['context'] = _build_expense_proposal_context(ctx.tool_history)
    merged['expense_reason'] = ctx.expense_reason
    return merged


_PURCHASE_SYSTEM_ARG_KEYS = frozenset({
    'employee_id', 'trace_id', 'item_name', 'requested_budget',
    'justification', 'context',
})


def _inject_purchase_budget(args: dict, ctx: _ExecutorContext) -> dict:
    merged = dict(args or {})
    leaked = set(args or {}).intersection(_LEAVE_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['employee_id'] = ctx.employee_id
    merged['trace_id'] = ctx.trace_id
    return merged


def _inject_purchase_semantics(args: dict, ctx: _ExecutorContext) -> dict:
    merged = dict(args or {})
    leaked = set(args or {}).intersection(_PURCHASE_SYSTEM_ARG_KEYS)
    if leaked:
        raise PlannerDecisionError(
            f'{ctx.tool_name} 不得在 arguments 中夹带系统字段 {sorted(leaked)}'
        )
    merged['item_name'] = ctx.purchase_item or ''
    merged['requested_budget'] = (
        str(ctx.purchase_budget) if ctx.purchase_budget is not None else ''
    )
    merged['justification'] = ctx.purchase_justification or ''
    return merged


def _purchase_fact_context(tool_history: list[dict]) -> dict:
    from app.services.purchase_facts_service import purchase_fact_context

    return purchase_fact_context(tool_history)


def _inject_purchase_proposal(args: dict, ctx: _ExecutorContext) -> dict:
    merged = _inject_purchase_semantics(args, ctx)
    merged['context'] = _purchase_fact_context(ctx.tool_history)
    return merged


# --- Proposal 后置钩子 ------------------------------------------------------

def _leave_proposal_post(parsed: dict, tool_name: str) -> dict:
    # ISO -> date 还原(详见 _restore_iso_date_fields 注释)。
    # 仅针对 action_proposal 这一个嵌套 dict;不修改 outer observation。
    return {
        'action_proposal': _restore_iso_date_fields(parsed.get('action_proposal')),
        'missing_fields': parsed.get('missing_fields', []),
    }


def _expense_proposal_post(parsed: dict, tool_name: str) -> dict:
    """把 expense proposal Tool 的结构化结果回写到 AgentState。"""
    return {
        'action_proposal': parsed.get('action_proposal'),
        'missing_fields': parsed.get('missing_fields', []),
    }


def _restore_purchase_decimal_fields(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return payload
    for key in ('requested_budget', 'available_budget'):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                from decimal import Decimal
                payload[key] = Decimal(value)
            except Exception:
                pass
    return payload


def _purchase_proposal_post(parsed: dict, tool_name: str) -> dict:
    return {
        'action_proposal': _restore_purchase_decimal_fields(parsed.get('action_proposal')),
        'missing_fields': parsed.get('missing_fields', []),
    }


def _is_completed_success(item: dict) -> bool:
    """成功去重的领域完成语义由 Provider Registry 决定。"""
    return DOMAIN_PROVIDER_REGISTRY.is_completed_success(item)


# --- 仅供只读企业 Tool 使用;Planner arguments 不得出现这些 key ---------
_LEAVE_SYSTEM_ARG_KEYS = frozenset({'employee_id', 'trace_id'})

# leave_proposal_tool 的系统字段与业务字段:全部由 Executor 从 Runtime Context 注入,
# 模型 arguments 中不得夹带任何一项(业务参数由受控链路基于原始问题解析)
_PROPOSAL_SYSTEM_ARG_KEYS = frozenset({
    'employee_id', 'trace_id', 'business_date',
    'start_date', 'end_date', 'reason', 'half_day',
})


# --- 注册表 -----------------------------------------------------------------

def _build_registry() -> dict[str, ToolSpec]:
    return {
        RAG_TOOL_NAME: ToolSpec(
            name=RAG_TOOL_NAME,
            executable_ref='rag_answer_tool',
            identity_required=False,
            resume_safe=True,
            system_arg_keys=frozenset(),
            pre_inject=_inject_rag,
        ),
        EVAL_TOOL_NAME: ToolSpec(
            name=EVAL_TOOL_NAME,
            executable_ref='eval_report_tool',
            identity_required=False,
            resume_safe=True,
            system_arg_keys=frozenset(),
        ),
        LEAVE_BALANCE_TOOL_NAME: ToolSpec(
            name=LEAVE_BALANCE_TOOL_NAME,
            executable_ref='leave_balance_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_LEAVE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='access_control',
            pre_inject=_inject_leave_read,
        ),
        LEAVE_REQUEST_TOOL_NAME: ToolSpec(
            name=LEAVE_REQUEST_TOOL_NAME,
            executable_ref='leave_request_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_LEAVE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='access_control',
            pre_inject=_inject_leave_read,
        ),
        LEAVE_PROPOSAL_TOOL_NAME: ToolSpec(
            name=LEAVE_PROPOSAL_TOOL_NAME,
            executable_ref='leave_proposal_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_PROPOSAL_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='business_action',
            pre_inject=_inject_leave_proposal,
            proposal_post=_leave_proposal_post,
        ),
        # P2-A Expense Workflow V1：Phase 3 注册 travel/invoice read Tool
        TRAVEL_RECORD_TOOL_NAME: ToolSpec(
            name=TRAVEL_RECORD_TOOL_NAME,
            executable_ref='travel_record_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_OAMCP_READ_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='access_control',
            pre_inject=_inject_oamcp_read,
        ),
        INVOICE_VERIFY_TOOL_NAME: ToolSpec(
            name=INVOICE_VERIFY_TOOL_NAME,
            executable_ref='invoice_verify_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_OAMCP_READ_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='access_control',
            pre_inject=_inject_oamcp_read,
        ),
        # P2-A Phase 7: expense_proposal_tool —— 受控业务动作生成 Proposal
        EXPENSE_PROPOSAL_TOOL_NAME: ToolSpec(
            name=EXPENSE_PROPOSAL_TOOL_NAME,
            executable_ref='expense_proposal_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_EXPENSE_CTCX_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='business_action',
            pre_inject=_inject_expense_proposal,
            proposal_post=_expense_proposal_post,
        ),
        # P2-A Phase 8: expense_status_tool —— Java 权威状态查询（source=Java）
        EXPENSE_STATUS_TOOL_NAME: ToolSpec(
            name=EXPENSE_STATUS_TOOL_NAME,
            executable_ref='expense_status_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_LEAVE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='access_control',
            pre_inject=_inject_oamcp_read,
        ),
        PURCHASE_BUDGET_TOOL_NAME: ToolSpec(
            name=PURCHASE_BUDGET_TOOL_NAME,
            executable_ref='purchase_budget_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_LEAVE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='business_action',
            pre_inject=_inject_purchase_budget,
        ),
        PURCHASE_POLICY_TOOL_NAME: ToolSpec(
            name=PURCHASE_POLICY_TOOL_NAME,
            executable_ref='purchase_policy_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_PURCHASE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='business_action',
            pre_inject=_inject_purchase_semantics,
        ),
        PURCHASE_PROPOSAL_TOOL_NAME: ToolSpec(
            name=PURCHASE_PROPOSAL_TOOL_NAME,
            executable_ref='purchase_proposal_tool',
            identity_required=True,
            resume_safe=True,
            system_arg_keys=_PURCHASE_SYSTEM_ARG_KEYS,
            no_employee_blocked_category='business_action',
            pre_inject=_inject_purchase_proposal,
            proposal_post=_purchase_proposal_post,
        ),
    }


_TOOL_REGISTRY: dict[str, ToolSpec] = _build_registry()


def is_tool_resume_safe(tool_name: str) -> bool:
    """返回明确的重放策略；未知 Tool 采用 fail-closed。"""
    spec = _TOOL_REGISTRY.get(tool_name)
    return bool(spec is not None and spec.resume_safe)


def _get_tool(tool_name: str):
    """每次执行时通过模块 globals() 解析工具(不缓存快照,保证测试 patch 生效)。

    可执行对象本身仍由 _TOOL_REGISTRY 中的 executable_ref 决定（决定哪个名字
    在本模块下被调用），但每次 invoke 都重新走 globals()，使得
    patch('app.agents.tool_executor_node.rag_answer_tool', ...) 等模块 attr
    patch 立即生效。
    """
    spec = _TOOL_REGISTRY.get(tool_name)
    if spec is None:
        raise ValueError(f'Unknown tool: {tool_name}')
    try:
        return globals()[spec.executable_ref]
    except KeyError as exc:
        raise ValueError(
            f'Tool {tool_name} 引用了未注册的模块属性 {spec.executable_ref}'
        ) from exc


def _blocked(state: dict, runtime: Runtime[AgentRuntimeContext], stop_reason: str, message: str,
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

    if stop_reason != 'request_timeout' and monotonic() >= runtime.context['deadline_monotonic']:
        return _blocked(
            state,
            runtime,
            'request_timeout',
            '当前任务处理超时，未继续执行工具。',
        )
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
            and _is_completed_success(item)
        ):
            return True
    return False


def tool_executor_node(state: dict, runtime: Runtime[AgentRuntimeContext]) -> dict:
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
    trace_id = runtime.context['trace_id']
    decision_raw = state.get('planner_decision')
    tool_call_count = state.get('tool_call_count', 0)
    tool_history = list(state.get('tool_history', []))

    if monotonic() >= runtime.context['deadline_monotonic']:
        return _blocked(
            state,
            runtime,
            'request_timeout',
            '当前任务处理超时，未继续执行工具。',
        )

    # 1. 结构再校验：仅处理 action=tool 的合法决策（不信赖 Planner 已校验）
    if not isinstance(decision_raw, dict):
        return _blocked(state, runtime, 'invalid_decision', '缺少合法的 Planner 决策，已拒绝执行。')
    try:
        decision = PlannerDecision.model_validate(decision_raw)
        decision.validate_decision()
    except (ValidationError, PlannerDecisionError) as exc:
        logger.warning('[%s] tool_executor 决策非法: %s', trace_id, exc)
        return _blocked(state, runtime, 'invalid_decision', f'Tool 决策非法，已拒绝执行：{exc}')
    if decision.action != 'tool':
        return _blocked(state, runtime, 'invalid_decision', 'Tool Executor 仅处理 action=tool 的决策。')

    # 解析 ToolSpec（registry 驱动）。未知 Tool 在 Planner schema 已拒绝，
    # 这里仍是防御性兜底：保证后续 _get_tool 不会抛 KeyError。
    spec = _TOOL_REGISTRY.get(decision.tool_name)
    if spec is None:
        return _blocked(state, runtime, 'invalid_decision',
                        f'当前 Tool 不在注册表中：{decision.tool_name}')

    # 2. 身份前置校验（即使 Capability Gate / Planner 已校验，Executor 独立确认）
    employee_id = runtime.context['employee_id'].strip()
    if spec.identity_required and not employee_id:
        logger.warning(
            '[%s] tool_executor 拒绝无 employee_id 的 Tool=%s',
            trace_id, decision.tool_name,
        )
        return _blocked(
            state, runtime,
            'not_allowed',
            '当前请求缺少员工身份，已拒绝执行。',
            tool_name=decision.tool_name,
            arguments=decision.arguments,
            category=spec.no_employee_blocked_category,
        )

    # 3. 权限再校验（即使 Planner 已校验，Executor 独立确认）
    if decision.tool_name == EVAL_TOOL_NAME and not runtime.context['allow_eval']:
        logger.warning('[%s] tool_executor 越权执行 %s 被拒绝', trace_id, EVAL_TOOL_NAME)
        return _blocked(state, runtime, 'not_allowed', 'eval_report_tool 需要管理员权限，已拒绝执行。',
                        tool_name=decision.tool_name, arguments=decision.arguments,
                        category='access_control')
    # 受控业务动作统一权限（Provider capability tools，V2 §十二 HITL）
    if decision.tool_name in DOMAIN_PROVIDER_REGISTRY.business_action_tools:
        if not runtime.context['allow_business_actions']:
            logger.warning('[%s] tool_executor 越权执行 %s 被拒绝',
                           trace_id, decision.tool_name)
            return _blocked(state, runtime, 'not_allowed',
                            '业务动作功能未启用，或当前请求无执行权限。',
                            tool_name=decision.tool_name, arguments=decision.arguments,
                            category='business_action')
        if runtime.context['business_date'] is None:
            logger.warning('[%s] tool_executor 拒绝执行 %s：无业务日期',
                           trace_id, decision.tool_name)
            return _blocked(state, runtime, 'not_allowed', '当前业务日期不可用。',
                            tool_name=decision.tool_name, arguments=decision.arguments,
                            category='business_action')

    # 领域 second gate：Provider 只能拒绝非法动作，不能扩大上游 capability gate。
    try:
        DOMAIN_PROVIDER_REGISTRY.validate_tool_call(
            decision.tool_name,
            decision.arguments or {},
            DomainContext.from_state(state),
        )
    except DomainToolCallRejected as exc:
        return _blocked(
            state,
            runtime,
            exc.reason_code,
            str(exc),
            tool_name=decision.tool_name,
            arguments=decision.arguments,
        )
    except DomainProviderAmbiguityError as exc:
        logger.warning('[%s] executor domain provider ambiguity: %s', trace_id, exc)
        return _blocked(
            state,
            runtime,
            'invalid_decision',
            '当前 Tool 同时属于多个业务领域，已拒绝执行。',
            tool_name=decision.tool_name,
            arguments=decision.arguments,
        )

    # 4. Tool 调用预算（基于实际发起执行的次数）
    if tool_call_count >= MAX_TOOL_CALLS:
        return _blocked(state, runtime, 'tool_call_budget_exhausted',
                        'Tool 调用预算已耗尽，无法继续执行工具。',
                        tool_name=decision.tool_name, arguments=decision.arguments)

    # 5. 成功签名去重：相同 tool + 相同 arguments 且已成功完成 → 阻止；
    #    error / timeout 历史不阻止，允许合理重试
    if _already_completed(decision, tool_history):
        return _blocked(state, runtime, 'already_completed',
                        '该 Tool 调用已成功完成（相同工具与相同参数），不重复执行。',
                        tool_name=decision.tool_name, arguments=decision.arguments)

    if monotonic() >= runtime.context['deadline_monotonic']:
        return _blocked(
            state,
            runtime,
            'request_timeout',
            '当前任务处理超时，未继续执行工具。',
            tool_name=decision.tool_name,
            arguments=decision.arguments,
        )

    # 6. 执行前计数：真正发起执行即消耗一次调用预算（成功/超时/异常都计数）
    tool_call_count += 1
    try:
        ctx = _ExecutorContext(
            tool_name=decision.tool_name,
            employee_id=employee_id,
            trace_id=trace_id,
            question=state.get('question', ''),
            business_date=runtime.context['business_date'],
            # 只使用当前请求第一次 Planner 决策冻结的值；不能让本轮
            # 最新 PlannerDecision 覆盖此前的 null 或有效原因。
            expense_reason=state.get('request_expense_reason'),
            expense_request_question=state.get('continuation_original_request'),
            purchase_item=state.get(
                'purchase_item',
                (state.get('planner_decision') or {}).get('purchase_item'),
            ),
            purchase_budget=state.get(
                'purchase_budget',
                (state.get('planner_decision') or {}).get('purchase_budget'),
            ),
            purchase_justification=state.get(
                'purchase_justification',
                (state.get('planner_decision') or {}).get('purchase_justification'),
            ),
            tool_history=tool_history,
        )
        if spec.pre_inject is not None:
            args = spec.pre_inject(decision.arguments or {}, ctx)
        else:
            args = dict(decision.arguments or {})
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
    # ToolSpec.proposal_post 钩子：proposal 风格 Tool 把结果同步回 AgentState，
    # 供最终响应与后续链路使用。
    if spec.proposal_post is not None:
        try:
            parsed = json.loads(observation) if isinstance(observation, str) else {}
        except json.JSONDecodeError:
            parsed = {}
        updates.update(spec.proposal_post(parsed, decision.tool_name))
    return updates
