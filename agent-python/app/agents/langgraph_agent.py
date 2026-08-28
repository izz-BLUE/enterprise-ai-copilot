"""
langgraph_agent.py —— LangGraph Agent 核心模块

提供两条状态图：
  1. 确定性路由图（use_planner=False）：safety → router → rag | eval | action | refuse，
     集成 Safety Guard + LangChain Tools + RAG Chain。
  2. Agent Loop 图（use_planner=True）：safety → planner ⇄ tool_executor → finalize，
     Planner 决策工具调用，Tool Executor 校验权限/预算/重复并执行；
     业务动作统一由 Planner 调用 leave_proposal_tool 走受控链路生成待确认草稿。
"""

import json
from datetime import date
from functools import lru_cache
from time import monotonic
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.agents.action_proposal_policy import PROPOSAL_TOOL_NAMES, is_confirmable_action_proposal
from app.agents.execution_history_policy import merge_execution_history
from app.agents.planner_node import planner_node
from app.agents.runtime_context import AgentRuntimeContext, ExecutionMode
from app.agents.tool_executor_node import tool_executor_node
from app.core.config import AGENT_REQUEST_TIMEOUT_SECONDS, REWRITE_MODE, logger
from app.guards.safety_guard import check_user_query_safety
from app.retrieval.query_rewriter import rewrite_query
from app.schemas.execution_recovery_schema import (
    ExecutionRecoveryMarker,
    new_execution_recovery_marker,
)
from app.schemas.external_wait_schema import ExternalResumePayload, ExternalWaitMarker
from app.schemas.hitl_schema import HitlResumePayload, HitlWaitMarker, proposal_action_type
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    RAG_TOOL_NAME,
)
from app.services.annual_leave_input_service import is_annual_leave_action_intent
from app.services.tool_calling_service import plan_annual_leave_action
from app.tools.rag_tools import eval_report_tool, rag_answer_tool

EVAL_KEYWORDS = ['评估', '通过率', 'pass_rate', '命中率', 'baseline', '回归', 'flaky']


class AgentState(TypedDict):
    question: str
    safe: bool
    route: str
    answer: str
    tool_result: dict
    sources: list
    reason: str
    category: str
    action_proposal: dict | None
    missing_fields: list[str]
    # Agent Loop P0：step_count = Planner 已完成的决策次数（Finish/Refuse 也算一次）；
    # tool_call_count = 通过执行前校验后，实际发起 Tool 执行的次数——
    # 无论最终成功、超时、Provider 异常还是 Tool 自身失败，只要真正发起执行就计数。
    step_count: int
    tool_call_count: int
    tool_history: list
    # P3-2：跨请求的有限任务执行摘要；不表示当前业务事实。
    execution_history: list
    observation: str
    planner_decision: dict | None
    stop_reason: str
    # Scoped Conversation Memory / Task Continuity P0 — Phase 2 (Read Path)。
    # memory_context 由 Java 侧基于 (trusted user_id, conversation_id) 复合 key
    # 仅在 ACTIVE 时注入；它属于不可信历史上下文，不会改变 Capability Gate、
    # 当前可见 Tool 集合或任何 trusted 系统字段（employee_id / business_date /
    # allow_eval / allow_business_actions）。
    memory_context: dict | None
    # P3-3：当前未完成 Planner-first execution 的严格恢复控制标记；不承载
    # user/employee/permission/date/trace/deadline 等可信 Runtime Context。
    execution_recovery: dict | None
    # P3-4: persisted correlation for the single user-confirmation wait.
    hitl_wait: dict | None
    hitl_result: dict | None
    # P3-5A: one durable external expense approval wait and its terminal result.
    external_wait: dict | None
    external_result: dict | None


def safety_node(state: AgentState) -> dict:
    question = state["question"]
    result = check_user_query_safety(question)

    if not result["safe"]:
        return {
            "safe": False,
            "route": "refuse",
            "answer": result["message"],
            "reason": result["reason"],
            "category": result["category"],
        }
    return {"safe": True, "reason": "", "category": "normal"}


def router_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict:
    if not state.get("safe", True):
        return {"route": "refuse"}

    question = state["question"]
    if any(kw in question.lower() for kw in EVAL_KEYWORDS):
        if runtime.context["allow_eval"]:
            return {"route": "eval"}
        return {
            "route": "refuse",
            "answer": "该问题涉及内部评估诊断能力，仅管理员可访问。",
            "category": "access_control",
            "reason": "",
        }
    if is_annual_leave_action_intent(question):
        if not runtime.context["allow_business_actions"]:
            return {
                "route": "refuse",
                "answer": "业务动作功能未启用，或当前请求无执行权限。",
                "category": "access_control",
                "reason": "",
            }
        if runtime.context["business_date"] is None:
            return {
                "route": "refuse",
                "answer": "当前业务日期不可用。",
                "category": "business_action",
                "reason": "",
            }
        return {"route": "action"}
    return {"route": "rag"}


def rag_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict:
    question = state["question"]

    # Query Rewrite（只改写检索用 query，不改 original_query）
    rewrite_result = rewrite_query(question, mode=REWRITE_MODE)
    retrieval_query = rewrite_result['rewritten_query']
    if rewrite_result['rewrite_applied']:
        logger.info('[%s] LangGraph query rewrite applied reason=%s',
                    runtime.context['trace_id'] or '-', rewrite_result['rewrite_reason'])

    # 用 rewritten_query 检索，但传给 tool 的仍是 original_query
    # tool 内部的 LangChain RAG chain 会用 question 做检索和 prompt
    # 这里我们直接用 rewritten_query 调用 tool，让检索更准
    result_str = rag_answer_tool.invoke({
        "question": retrieval_query,
        "original_question": question,
        "trace_id": runtime.context['trace_id'],
    })
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        parsed = {"answer": result_str, "success": True, "sources": []}
    return {
        "answer": parsed.get("answer", ""),
        "tool_result": parsed,
        "sources": parsed.get("sources", []),
    }


def _pct(val) -> str:
    """将 0~1 的小数转为百分数字符串，None 返回 'N/A'。"""
    if val is None:
        return 'N/A'
    return f'{val * 100:.0f}%'


def eval_node(state: AgentState) -> dict:
    result_str = eval_report_tool.invoke({"report_type": "all"})
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        parsed = {"error": result_str}

    ret = parsed.get("retrieval", {})
    gen = parsed.get("generation", {})
    summary_parts = []
    if ret and not ret.get("error"):
        ab_passed = ret.get("passed", 0)
        ab_total = ret.get("answerable_cases", 0)
        na_total = ret.get("no_answer_cases", 0)
        total = ret.get("total", 0)
        rp = _pct(ret.get("final_pass_rate"))
        summary_parts.append(
            f'检索评估: answerable {ab_passed}/{ab_total} 通过, '
            f'no-answer {na_total} 个未计入通过率, '
            f'总用例 {total}, final_pass_rate={rp}'
        )
    if gen and not gen.get("error"):
        gen_passed = gen.get("passed", 0)
        gen_total = gen.get("total", 0)
        rp = _pct(gen.get("pass_rate"))
        srp = _pct(gen.get("stable_pass_rate"))
        flaky = gen.get("flaky_count", 0)
        summary_parts.append(
            f'生成评估: {gen_passed}/{gen_total} 通过, '
            f'pass_rate={rp}, stable_pass_rate={srp}, '
            f'flaky={flaky}'
        )

    return {
        "answer": "；".join(summary_parts) if summary_parts else str(parsed),
        "tool_result": parsed,
    }


def action_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict:
    business_date = runtime.context["business_date"]
    if business_date is None:
        return {
            "route": "error",
            "answer": "当前业务日期不可用。",
            "category": "business_action",
            "reason": "",
            "missing_fields": [],
            "action_proposal": None,
        }
    result = plan_annual_leave_action(
        state["question"],
        business_date=business_date,
        trace_id=runtime.context["trace_id"],
    )
    if result.kind == "clarification":
        return {
            "route": "action",
            "answer": result.clarification.question,
            "category": "business_action",
            "reason": "",
            "missing_fields": result.clarification.missing_fields,
            "action_proposal": None,
        }
    if result.kind == "proposal":
        return {
            "route": "action",
            "answer": "我已生成一份模拟年假申请草稿，请确认后提交。",
            "category": "business_action",
            "reason": "",
            "missing_fields": [],
            "action_proposal": result.proposal.model_dump(),
        }
    return {
        "route": "error",
        "answer": "暂时无法生成申请草稿，请检查信息后重试。",
        "category": "business_action",
        "reason": "",
        "missing_fields": [],
        "action_proposal": None,
    }


def refuse_node(state: AgentState) -> dict:
    answer = state.get("answer", "")
    if not answer:
        answer = "抱歉，我不能协助处理该请求。"
    return {"answer": answer}


def compile_agent_graph(checkpointer: Any | None = None):
    """编译确定性 Graph；调用方可注入进程级 LangGraph Checkpointer。"""
    graph = StateGraph(AgentState, context_schema=AgentRuntimeContext)

    graph.add_node("safety_node", safety_node)
    graph.add_node("router_node", router_node)
    graph.add_node("rag_node", rag_node)
    graph.add_node("eval_node", eval_node)
    graph.add_node("action_node", action_node)
    graph.add_node("refuse_node", refuse_node)

    graph.add_edge(START, "safety_node")
    graph.add_edge("safety_node", "router_node")

    graph.add_conditional_edges(
        "router_node",
        lambda state: state.get("route", "rag"),
        {
            "rag": "rag_node",
            "eval": "eval_node",
            "action": "action_node",
            "refuse": "refuse_node",
        },
    )

    graph.add_edge("rag_node", END)
    graph.add_edge("eval_node", END)
    graph.add_edge("action_node", END)
    graph.add_edge("refuse_node", END)

    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def build_agent_graph():
    """无 Checkpointer 的兼容 Graph，供普通单元测试与 DISABLED 模式复用。"""
    return compile_agent_graph()


def prepare_hitl_node(state: AgentState) -> dict:
    """Create the deterministic checkpoint marker immediately before approval."""
    if not is_confirmable_action_proposal(state):
        raise RuntimeError('当前状态不是可确认的业务 Proposal，禁止进入 HITL')

    try:
        recovery = ExecutionRecoveryMarker.model_validate(
            state.get('execution_recovery'),
        )
        action_type = proposal_action_type(state.get('action_proposal'))
        if action_type is None:
            raise ValueError('action_proposal action_type 无效')
        wait = HitlWaitMarker.for_execution(recovery.execution_id, action_type)
    except Exception as exc:
        logger.error('HITL wait marker 构造失败 error_type=%s', type(exc).__name__)
        raise RuntimeError('HITL wait marker 无效') from None

    return {
        'hitl_wait': wait.model_dump(),
        'route': 'action',
        'category': 'business_action',
    }


def approval_node(state: AgentState) -> dict:
    """Pause for Java's authoritative decision and deterministically apply it."""
    try:
        wait = HitlWaitMarker.model_validate(state.get('hitl_wait'))
    except Exception:
        logger.error('HITL approval marker 校验失败')
        raise RuntimeError('HITL wait marker 无效') from None

    # This node must remain side-effect free before interrupt(): LangGraph 1.2.9
    # re-executes the node from its beginning when Command(resume=...) arrives.
    resumed = interrupt(wait.model_dump())
    try:
        payload = HitlResumePayload.model_validate(resumed)
    except Exception:
        logger.warning('HITL authoritative resume payload 校验失败')
        raise RuntimeError('HITL resume payload 无效') from None

    if (
        payload.wait_id != wait.wait_id
        or payload.execution_id != wait.execution_id
        or payload.action_type != wait.action_type
    ):
        logger.warning('HITL authoritative resume correlation mismatch')
        raise RuntimeError('HITL resume correlation 不匹配')

    stop_reason = {
        'CONFIRMED': 'hitl_confirmed',
        'CANCELLED': 'hitl_cancelled',
        'EXPIRED': 'hitl_expired',
        'REJECTED': 'hitl_rejected',
    }[payload.decision]
    return {
        'hitl_result': payload.model_dump(),
        'action_proposal': None,
        'missing_fields': [],
        'answer': payload.message,
        'route': 'action',
        'category': 'business_action',
        'reason': '',
        'stop_reason': stop_reason,
    }


def _should_wait_for_external_approval(state: AgentState) -> bool:
    """Route only a confirmed expense with its Java ExpenseClaim id."""
    try:
        result = HitlResumePayload.model_validate(state.get('hitl_result'))
    except Exception:
        raise RuntimeError('HITL result 无效，禁止进入 external wait 路由') from None
    if result.decision != 'CONFIRMED' or result.action_type != 'EXPENSE_CLAIM':
        return False
    if not result.request_id:
        raise RuntimeError('已确认的报销动作缺少本地 ExpenseClaim request_id')
    return True


def _approval_route(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> str:
    """Select external lifecycle from Java's trusted context only."""
    if runtime.context.get('execution_mode', 'LEGACY_SINGLE') == 'TASK_RUNTIME':
        return 'finalize_node'
    return (
        'prepare_external_wait_node'
        if _should_wait_for_external_approval(state)
        else 'finalize_node'
    )


def prepare_external_wait_node(state: AgentState) -> dict:
    """Persist deterministic correlation without calling any external system."""
    if not _should_wait_for_external_approval(state):
        raise RuntimeError('当前状态不允许进入 external approval wait')
    try:
        recovery = ExecutionRecoveryMarker.model_validate(state.get('execution_recovery'))
        hitl_result = HitlResumePayload.model_validate(state.get('hitl_result'))
        if hitl_result.request_id is None:
            raise ValueError('request_id missing')
        wait = ExternalWaitMarker.for_execution(
            recovery.execution_id,
            hitl_result.request_id,
        )
    except Exception as exc:
        logger.error('External wait marker 构造失败 error_type=%s', type(exc).__name__)
        raise RuntimeError('External wait marker 无效') from None
    return {
        'external_wait': wait.model_dump(),
        'external_result': None,
        'answer': '报销申请已提交，正在等待外部审批。',
        'route': 'action',
        'category': 'business_action',
        'reason': '',
    }


def external_wait_node(state: AgentState) -> dict:
    """Consume exactly one correlated Java-authoritative OA terminal decision."""
    try:
        wait = ExternalWaitMarker.model_validate(state.get('external_wait'))
    except Exception:
        logger.error('External approval marker 校验失败')
        raise RuntimeError('External wait marker 无效') from None

    # LangGraph 1.2.9 re-enters this node from the beginning on resume.  Keep
    # everything before interrupt() deterministic and side-effect free.
    resumed = interrupt(wait.model_dump())
    try:
        payload = ExternalResumePayload.model_validate(resumed)
    except Exception:
        logger.warning('External authoritative resume payload 校验失败')
        raise RuntimeError('External resume payload 无效') from None
    if (
        payload.wait_id != wait.wait_id
        or payload.execution_id != wait.execution_id
        or payload.action_type != wait.action_type
        or payload.request_id != wait.request_id
    ):
        logger.warning('External authoritative resume correlation mismatch')
        raise RuntimeError('External resume correlation 不匹配')
    return {
        'external_result': payload.model_dump(),
        'action_proposal': None,
        'missing_fields': [],
        'answer': payload.message,
        'route': 'action',
        'category': 'business_action',
        'reason': '',
        'stop_reason': (
            'external_approved' if payload.decision == 'APPROVED' else 'external_rejected'
        ),
    }


def compile_agent_loop_graph(
    checkpointer: Any | None = None,
    *,
    enable_hitl: bool = True,
):
    """最小有限 Agent Loop：safety → planner ⇄ tool_executor → finalize。

    Planner-first 拓扑。Safety 保留 pre-Planner 拦截边界：
    - safe=true → planner_node；safe=false → finalize_node（unsafe 输入不得调用 Planner LLM）
    - 不再经 router_node，也没有 action_node 特殊出口（业务动作统一由
      Planner 决策调用 leave_proposal_tool，经 Tool Executor 走受控链路）：
      stop_reason=continue → tool_executor → planner
      其他终止（task_complete / refused / not_allowed / provider_error /
      invalid_decision / step_budget_exhausted）→ finalize_node → END
    """
    graph = StateGraph(AgentState, context_schema=AgentRuntimeContext)

    graph.add_node("safety_node", safety_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("tool_executor_node", tool_executor_node)
    if enable_hitl:
        graph.add_node("prepare_hitl_node", prepare_hitl_node)
        graph.add_node("approval_node", approval_node)
        graph.add_node("prepare_external_wait_node", prepare_external_wait_node)
        graph.add_node("external_wait_node", external_wait_node)
    graph.add_node("finalize_node", finalize_node)

    graph.add_edge(START, "safety_node")

    graph.add_conditional_edges(
        "safety_node",
        lambda state: "planner_node" if state.get("safe", True) else "finalize_node",
        {
            "planner_node": "planner_node",
            "finalize_node": "finalize_node",
        },
    )

    def planner_route(state: AgentState) -> str:
        if state.get("stop_reason") == "continue":
            return "tool_executor_node"
        if enable_hitl and is_confirmable_action_proposal(state):
            return "prepare_hitl_node"
        return "finalize_node"

    planner_routes = {
        "tool_executor_node": "tool_executor_node",
        "finalize_node": "finalize_node",
    }
    if enable_hitl:
        planner_routes["prepare_hitl_node"] = "prepare_hitl_node"
    graph.add_conditional_edges("planner_node", planner_route, planner_routes)

    graph.add_edge("tool_executor_node", "planner_node")
    if enable_hitl:
        graph.add_edge("prepare_hitl_node", "approval_node")
        graph.add_conditional_edges(
            "approval_node",
            _approval_route,
            {
                "prepare_external_wait_node": "prepare_external_wait_node",
                "finalize_node": "finalize_node",
            },
        )
        graph.add_edge("prepare_external_wait_node", "external_wait_node")
        graph.add_edge("external_wait_node", "finalize_node")
    graph.add_edge("finalize_node", END)

    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def build_agent_loop_graph():
    """无 Checkpointer 的兼容 Planner-first Graph。"""
    # Dynamic interrupt requires a checkpointer to be resumable.  The legacy
    # no-checkpoint graph remains a deterministic compatibility graph used by
    # unit tests and DISABLED mode; POSTGRES production compiles the HITL graph
    # through CheckpointRuntime with the default enable_hitl=True.
    return compile_agent_loop_graph(enable_hitl=False)


# P2-A/P3-4: proposal 风格 Tool 集合（leave / expense）。
# - leave_proposal_tool / expense_proposal_tool 的最后成功 → route=action
# - **不**包含 travel_record_tool / invoice_verify_tool / expense_status_tool
#   （这些只读/查询 Tool 单独成功不应触发 action 语义）
_PROPOSAL_TOOL_NAMES = PROPOSAL_TOOL_NAMES


def _finalize_action_proposal(state: dict) -> dict:
    """最终响应前的确定性 postcondition：防止 stale action_proposal 泄漏。

    仅当同时满足以下条件才保留 action_proposal / missing_fields：
    - stop_reason == task_complete（Planner 以 finish 结束）
    - 最后一次成功执行的 Tool 是 leave_proposal_tool
    （proposal 或 clarification 的合法最终语义）
    否则清空 action_proposal 与 missing_fields。
    """
    if state.get('stop_reason') != 'task_complete':
        state['action_proposal'] = None
        state['missing_fields'] = []
        return state
    # Clarification keeps its missing_fields with action_proposal=None.  A
    # materialized proposal must use the same shared readiness predicate as
    # HITL routing, otherwise stale proposals could leak into the response.
    if state.get('action_proposal') is not None and not is_confirmable_action_proposal(state):
        state['action_proposal'] = None
        state['missing_fields'] = []
    return state


# Agent Loop 公共响应契约收敛：仅 use_planner=True 时生效，确定性派生 route / category。
# 不依赖 Planner / LLM；输入只是 AgentState 的最终字段。
# route 取值集合：rag / eval / action / agent / refuse / error
# category 取值集合：normal / business_action / access_control / safety 类别 / error / overloaded / input_error
# reason 仅在安全 / 权限场景保留；正常 / 业务动作场景清空，避免泄漏 prompt 内调试字段。
#
# 程序层（planner_node / tool_executor_node）在 not_allowed 分支会显式写入 state['category']
# （access_control / business_action），本函数优先保留；仅当程序层未写时，才按 stop_reason 兜底。


def _finalize_response_contract(state: dict) -> dict:
    """Agent Loop 公共响应契约：基于 safe / stop_reason / tool_history
    确定性收敛 route / category / reason；不改变 agent / planner / executor 内部行为。

    优先级：
    1. safe=False（Safety 前置拦截）→ route=refuse，保留 Safety 的 category/reason。
    2. stop_reason=task_complete：
       - 最后成功 Tool=leave_proposal_tool → route=action, category=business_action
       - 实际执行 Tool 全部是 rag_answer_tool → route=rag
       - 实际执行 Tool 全部是 eval_report_tool → route=eval
       - 无 Tool / 仅企业只读 Tool / 混合 Tool / 其他正常情况 → route=agent, category=normal
    3. stop_reason=refused → route=refuse, category=normal。
    4. stop_reason=not_allowed → route=refuse；category 优先取程序层已写入的
       access_control / business_action（planner_node / tool_executor_node 显式标注），
       缺省按 access_control。
    5. provider_error / invalid_decision / step_budget_exhausted
       以及任何未识别的 stop_reason → route=error, category=error。

    安全规则不放松：仅确定性地按上述规则改写 route / category / reason；
    action_proposal / missing_fields 是否清空由 _finalize_action_proposal 决定，
    本函数不在此基础上放宽。
    """
    safe = state.get('safe', True)
    stop_reason = state.get('stop_reason', '')

    # 1. Safety 前置拦截：保留 Safety 节点写入的 category / reason，不覆盖。
    if not safe:
        state['route'] = 'refuse'
        state['reason'] = state.get('reason', '')
        return state

    # Java has already decided the business action.  The graph must not ask the
    # LLM to reinterpret that result during finalization.
    if stop_reason in {
        'hitl_confirmed', 'hitl_cancelled', 'hitl_expired', 'hitl_rejected',
        'external_approved', 'external_rejected',
    }:
        state['route'] = 'action'
        state['category'] = 'business_action'
        state['reason'] = ''
        state['action_proposal'] = None
        state['missing_fields'] = []
        return state

    # 2. task_complete：根据 tool_history 收敛 route。
    if stop_reason == 'task_complete':
        executed = [
            entry.get('tool_name')
            for entry in (state.get('tool_history') or [])
            if entry.get('tool_name')
        ]
        last_success: str | None = None
        for entry in (state.get('tool_history') or []):
            if entry.get('status') == 'success':
                last_success = entry.get('tool_name')

        if last_success in _PROPOSAL_TOOL_NAMES:
            state['route'] = 'action'
            state['category'] = 'business_action'
        elif executed and all(name == RAG_TOOL_NAME for name in executed):
            state['route'] = 'rag'
            state['category'] = 'normal'
        elif executed and all(name == EVAL_TOOL_NAME for name in executed):
            state['route'] = 'eval'
            state['category'] = 'normal'
        else:
            # 无 Tool / 仅企业只读 Tool / 混合 Tool / 其他正常情况
            state['route'] = 'agent'
            state['category'] = 'normal'
        # 正常完成路径：reason 不暴露 prompt 内部字段；业务动作缺字段是合法语义。
        if state['route'] != 'action':
            state['reason'] = ''
        return state

    # 3. refused：route=refuse, category=normal。
    if stop_reason == 'refused':
        state['route'] = 'refuse'
        state['category'] = 'normal'
        state['reason'] = state.get('reason', '')
        return state

    # 4. not_allowed：route=refuse；category 优先保留程序层已显式写入的语义。
    if stop_reason == 'not_allowed':
        existing_category = state.get('category', '')
        state['route'] = 'refuse'
        if existing_category in ('access_control', 'business_action'):
            # planner_node / tool_executor_node 已显式标注，保留语义
            return state
        # 兜底：程序层未显式标注时，按 access_control（与权限拒绝语义一致）
        state['category'] = 'access_control'
        return state

    # 5. provider_error / invalid_decision / step_budget_exhausted / 未识别异常 → error。
    state['route'] = 'error'
    state['category'] = 'error'
    state['reason'] = ''
    return state


def finalize_node(state: AgentState) -> dict:
    """Planner-first Graph 的唯一最终化节点。

    两个响应 finalizer 与 execution_history merge 在 Checkpoint 写入前执行，
    确保 Graph 返回值与最后一个节点保存的状态完全一致。函数本身保持幂等，
    便于兼容性测试复用。
    """
    state = _finalize_action_proposal(state)
    state = _finalize_response_contract(state)
    # 只在 Graph 内、最终 Checkpoint 写入前合并；不把 history 接入当前
    # tool dedup、ExpenseProposalContext 或 Memory Trigger。
    state['execution_history'] = merge_execution_history(
        state.get('execution_history', []),
        state.get('tool_history', []),
    )
    return state


def run_langgraph_agent(
    question: str,
    allow_eval: bool = False,
    allow_business_actions: bool = False,
    business_date: date | None = None,
    trace_id: str = '',
    use_planner: bool = False,
    employee_id: str = '',
    memory_context: dict | None = None,
    execution_history: list[dict] | None = None,
    graph: Any | None = None,
    runtime_thread_id: str | None = None,
    execution_mode: ExecutionMode = 'LEGACY_SINGLE',
) -> dict:
    """运行 LangGraph Agent。

    use_planner=False 时使用确定性路由（safety → router → rag|eval|action|refuse）。
    use_planner=True 时启用 Agent Loop：safety → planner ⇄ tool_executor → finalize，
    Planner 自行决定工具调用与完成；业务动作通过 leave_proposal_tool
    走受控链路生成待确认草稿，不再使用 router_node 与 action_node；
    Planner-first 的两层 finalization 在 Graph 内的 finalize_node 执行，且在
    PostgreSQL Checkpoint 写入前完成；deterministic graph 保持原行为不变。
    employee_id 由 Java 侧身份校验后注入，仅供只读企业 Tool 使用；Planner 不可见。

    memory_context 为可选的 Phase 2 内存上下文：仅在 Java 侧 (userId, conversationId)
    命中 ACTIVE 记录时由调用方通过内部请求 body 注入；缺省 None 等价于历史行为
    （Planner 不渲染 memory block）。
    execution_history 为调用方已按 Checkpoint + ACTIVE Memory 过滤并校验的历史；
    普通无 Checkpointer 单元测试缺省为空，确定性 Graph 始终不加载跨请求 history。
    runtime_thread_id 是已追加拓扑后缀的 LangGraph thread_id，只在 POSTGRES
    Checkpoint 模式传入。该函数始终创建一次新的 AgentState；未完成执行的
    继续运行由 resume_langgraph_agent 显式负责。
    """
    if graph is None:
        graph = build_agent_loop_graph() if use_planner else build_agent_graph()
    initial: AgentState = {
        "question": question, "safe": True, "route": "",
        "answer": "", "tool_result": {}, "sources": [],
        "reason": "", "category": "",
        "action_proposal": None,
        "missing_fields": [],
        "step_count": 0,
        "tool_call_count": 0,
        "tool_history": [],
        "execution_history": list(execution_history or []) if use_planner else [],
        "observation": "",
        "planner_decision": None,
        "stop_reason": "",
        "memory_context": memory_context,
        "execution_recovery": (
            new_execution_recovery_marker(question, business_date, employee_id)
            if use_planner else None
        ),
        "hitl_wait": None,
        "hitl_result": None,
        "external_wait": None,
        "external_result": None,
    }
    runtime_context = _build_runtime_context(
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
        business_date=business_date,
        trace_id=trace_id,
        employee_id=employee_id,
        execution_mode=execution_mode,
    )
    config = _build_graph_config(runtime_thread_id, trace_id)
    if runtime_thread_id:
        result = dict(
            graph.invoke(initial, config=config, context=runtime_context, durability='sync')
        )
    else:
        result = dict(graph.invoke(initial, config=config, context=runtime_context))
    return result


def _build_runtime_context(
    *,
    allow_eval: bool,
    allow_business_actions: bool,
    business_date: date | None,
    trace_id: str,
    employee_id: str,
    execution_mode: ExecutionMode = 'LEGACY_SINGLE',
) -> AgentRuntimeContext:
    """Build trusted context from the current request only."""
    return {
        "employee_id": employee_id,
        "allow_eval": allow_eval,
        "allow_business_actions": allow_business_actions,
        "business_date": business_date,
        "trace_id": trace_id,
        "deadline_monotonic": monotonic() + AGENT_REQUEST_TIMEOUT_SECONDS,
        "execution_mode": execution_mode,
    }


def _build_graph_config(runtime_thread_id: str | None, trace_id: str) -> dict:
    """Build config with only current-request observability and thread routing."""
    config: dict = {}
    if trace_id:
        config['metadata'] = {'business_trace_id': trace_id}
    if runtime_thread_id:
        config['configurable'] = {'thread_id': runtime_thread_id}
    return config


def resume_langgraph_agent(
    *,
    graph: Any,
    runtime_thread_id: str,
    allow_eval: bool = False,
    allow_business_actions: bool = False,
    business_date: date | None = None,
    trace_id: str = '',
    employee_id: str = '',
    execution_mode: ExecutionMode = 'LEGACY_SINGLE',
) -> dict:
    """Resume the latest pending Planner-first checkpoint with fresh trusted context."""
    runtime_context = _build_runtime_context(
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
        business_date=business_date,
        trace_id=trace_id,
        employee_id=employee_id,
        execution_mode=execution_mode,
    )
    return dict(graph.invoke(
        None,
        config=_build_graph_config(runtime_thread_id, trace_id),
        context=runtime_context,
        durability='sync',
    ))


def resume_hitl_langgraph_agent(
    *,
    graph: Any,
    runtime_thread_id: str,
    payload: HitlResumePayload,
    allow_eval: bool = False,
    allow_business_actions: bool = False,
    business_date: date | None = None,
    trace_id: str = '',
    employee_id: str = '',
    execution_mode: ExecutionMode = 'LEGACY_SINGLE',
) -> dict:
    """Resume the active approval interrupt with Java's validated result."""
    runtime_context = _build_runtime_context(
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
        business_date=business_date,
        trace_id=trace_id,
        employee_id=employee_id,
        execution_mode=execution_mode,
    )
    return dict(graph.invoke(
        Command(resume=payload.model_dump()),
        config=_build_graph_config(runtime_thread_id, trace_id),
        context=runtime_context,
        durability='sync',
    ))


def resume_external_langgraph_agent(
    *,
    graph: Any,
    runtime_thread_id: str,
    payload: ExternalResumePayload,
    allow_eval: bool = False,
    allow_business_actions: bool = False,
    business_date: date | None = None,
    trace_id: str = '',
    employee_id: str = '',
    execution_mode: ExecutionMode = 'LEGACY_SINGLE',
) -> dict:
    """Resume the active external interrupt with fresh trusted Runtime Context."""
    runtime_context = _build_runtime_context(
        allow_eval=allow_eval,
        allow_business_actions=allow_business_actions,
        business_date=business_date,
        trace_id=trace_id,
        employee_id=employee_id,
        execution_mode=execution_mode,
    )
    return dict(graph.invoke(
        Command(resume=payload.model_dump()),
        config=_build_graph_config(runtime_thread_id, trace_id),
        context=runtime_context,
        durability='sync',
    ))
