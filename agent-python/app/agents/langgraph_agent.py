"""
langgraph_agent.py —— LangGraph Agent 核心模块

实现 safety → router → (rag | eval | refuse) 的最小状态图，
集成 Safety Guard + LangChain Tools + RAG Chain。
"""

import json
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import REWRITE_MODE, logger
from app.guards.safety_guard import check_user_query_safety
from app.retrieval.query_rewriter import rewrite_query
from app.services.annual_leave_input_service import is_annual_leave_action_intent
from app.services.tool_calling_service import plan_annual_leave_action
from app.tools.rag_tools import eval_report_tool, rag_answer_tool

from app.agents.planner_node import planner_node
from app.agents.tool_executor_node import tool_executor_node
from app.schemas.planner_schema import LEAVE_PROPOSAL_TOOL_NAME

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
    allow_eval: bool
    allow_business_actions: bool
    business_date: date | None
    trace_id: str
    employee_id: str  # 企业 Tool P0：Java 注入的身份字段；Tool Executor 注入到只读企业 Tool
    action_proposal: dict | None
    missing_fields: list[str]
    # Agent Loop P0 预留：step_count = Planner 已完成的决策次数（Finish/Refuse 也算一次）；
    # tool_call_count = 通过执行前校验后，实际发起 Tool 执行的次数——
    # 无论最终成功、超时、Provider 异常还是 Tool 自身失败，只要真正发起执行就计数；
    # （本阶段无 Tool Executor，保持 0，不递增）
    step_count: int
    tool_call_count: int
    tool_history: list
    observation: str
    planner_decision: dict | None
    stop_reason: str


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


def router_node(state: AgentState) -> dict:
    if not state.get("safe", True):
        return {"route": "refuse"}

    question = state["question"]
    if any(kw in question.lower() for kw in EVAL_KEYWORDS):
        if state.get("allow_eval", False):
            return {"route": "eval"}
        return {
            "route": "refuse",
            "answer": "该问题涉及内部评估诊断能力，仅管理员可访问。",
            "category": "access_control",
            "reason": "",
        }
    if is_annual_leave_action_intent(question):
        if not state.get("allow_business_actions", False):
            return {
                "route": "refuse",
                "answer": "业务动作功能未启用，或当前请求无执行权限。",
                "category": "access_control",
                "reason": "",
            }
        if state.get("business_date") is None:
            return {
                "route": "refuse",
                "answer": "当前业务日期不可用。",
                "category": "business_action",
                "reason": "",
            }
        return {"route": "action"}
    return {"route": "rag"}


def rag_node(state: AgentState) -> dict:
    question = state["question"]

    # Query Rewrite（只改写检索用 query，不改 original_query）
    rewrite_result = rewrite_query(question, mode=REWRITE_MODE)
    retrieval_query = rewrite_result['rewritten_query']
    if rewrite_result['rewrite_applied']:
        logger.info('[%s] LangGraph query rewrite applied reason=%s',
                    state.get('trace_id', '-'), rewrite_result['rewrite_reason'])

    # 用 rewritten_query 检索，但传给 tool 的仍是 original_query
    # tool 内部的 LangChain RAG chain 会用 question 做检索和 prompt
    # 这里我们直接用 rewritten_query 调用 tool，让检索更准
    result_str = rag_answer_tool.invoke({
        "question": retrieval_query,
        "original_question": question,
        "trace_id": state.get('trace_id', ''),
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


def action_node(state: AgentState) -> dict:
    business_date = state.get("business_date")
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
        trace_id=state.get("trace_id", ""),
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


def build_agent_graph():
    graph = StateGraph(AgentState)

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

    return graph.compile()


def build_agent_loop_graph():
    """最小有限 Agent Loop：safety → planner ⇄ tool_executor。

    Planner-first 拓扑。Safety 保留 pre-Planner 拦截边界：
    - safe=true → planner_node；safe=false → END（unsafe 输入不得调用 Planner LLM）
    - 不再经 router_node，也没有 action_node 特殊出口（业务动作统一由
      Planner 决策调用 leave_proposal_tool，经 Tool Executor 走受控链路）：
      stop_reason=continue → tool_executor → planner
      其他终止（task_complete / refused / not_allowed / provider_error /
      invalid_decision / step_budget_exhausted）→ END
    """
    graph = StateGraph(AgentState)

    graph.add_node("safety_node", safety_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("tool_executor_node", tool_executor_node)

    graph.add_edge(START, "safety_node")

    graph.add_conditional_edges(
        "safety_node",
        lambda state: "planner_node" if state.get("safe", True) else END,
        {
            "planner_node": "planner_node",
            END: END,
        },
    )

    graph.add_conditional_edges(
        "planner_node",
        lambda state: (
            "tool_executor_node" if state.get("stop_reason") == "continue"
            else END
        ),
        {
            "tool_executor_node": "tool_executor_node",
            END: END,
        },
    )

    graph.add_edge("tool_executor_node", "planner_node")

    return graph.compile()


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
    last_success: str | None = None
    for entry in state.get('tool_history', []) or []:
        if entry.get('status') == 'success':
            last_success = entry.get('tool_name')
    if last_success != LEAVE_PROPOSAL_TOOL_NAME:
        state['action_proposal'] = None
        state['missing_fields'] = []
    return state


def run_langgraph_agent(
    question: str,
    allow_eval: bool = False,
    allow_business_actions: bool = False,
    business_date: date | None = None,
    trace_id: str = '',
    use_planner: bool = False,
    employee_id: str = '',
) -> dict:
    """运行 LangGraph Agent。

    use_planner=False 时使用确定性路由（safety → router → rag|eval|action|refuse）。
    use_planner=True 时启用 Agent Loop：safety → planner ⇄ tool_executor，
    Planner 自行决定工具调用与完成；业务动作通过 leave_proposal_tool
    走受控链路生成待确认草稿，不再使用 router_node 与 action_node；
    返回前应用 action_proposal finalization（仅 Agent Loop 路径，
    deterministic graph 保持原行为不变）。
    employee_id 由 Java 侧身份校验后注入，仅供只读企业 Tool 使用；Planner 不可见。
    """
    graph = build_agent_loop_graph() if use_planner else build_agent_graph()
    initial: AgentState = {
        "question": question, "safe": True, "route": "",
        "answer": "", "tool_result": {}, "sources": [],
        "reason": "", "category": "",
        "allow_eval": allow_eval,
        "allow_business_actions": allow_business_actions,
        "business_date": business_date,
        "trace_id": trace_id,
        "employee_id": employee_id,
        "action_proposal": None,
        "missing_fields": [],
        "step_count": 0,
        "tool_call_count": 0,
        "tool_history": [],
        "observation": "",
        "planner_decision": None,
        "stop_reason": "",
    }
    # LangSmith metadata：业务 trace_id 仅用于关联定位，不覆盖 LangSmith 自身 Trace ID；
    # 动态字段（step_count / tool_call_count / stop_reason）随最终 state 出现在 run output。
    config: dict = {"metadata": {"business_trace_id": trace_id}} if trace_id else {}
    result = dict(graph.invoke(initial, config=config))
    if use_planner:
        result = _finalize_action_proposal(result)
    return result
