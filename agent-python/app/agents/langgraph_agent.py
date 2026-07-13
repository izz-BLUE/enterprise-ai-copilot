"""
langgraph_agent.py —— LangGraph Agent 核心模块

实现 safety → router → (rag | eval | refuse) 的最小状态图，
集成 Safety Guard + LangChain Tools + RAG Chain。
"""

import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import REWRITE_MODE, logger
from app.guards.safety_guard import check_user_query_safety
from app.retrieval.query_rewriter import rewrite_query
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
    allow_eval: bool
    trace_id: str


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
    graph.add_node("refuse_node", refuse_node)

    graph.add_edge(START, "safety_node")
    graph.add_edge("safety_node", "router_node")

    graph.add_conditional_edges(
        "router_node",
        lambda state: state.get("route", "rag"),
        {"rag": "rag_node", "eval": "eval_node", "refuse": "refuse_node"},
    )

    graph.add_edge("rag_node", END)
    graph.add_edge("eval_node", END)
    graph.add_edge("refuse_node", END)

    return graph.compile()


def run_langgraph_agent(
    question: str, allow_eval: bool = False, trace_id: str = '',
) -> dict:
    graph = build_agent_graph()
    initial: AgentState = {
        "question": question, "safe": True, "route": "",
        "answer": "", "tool_result": {}, "sources": [],
        "reason": "", "category": "",
        "allow_eval": allow_eval,
        "trace_id": trace_id,
    }
    return dict(graph.invoke(initial))
