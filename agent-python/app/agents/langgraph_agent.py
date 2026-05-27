"""
langgraph_agent.py —— LangGraph Agent 核心模块

实现 safety → router → (rag | eval | refuse) 的最小状态图，
集成 Safety Guard + LangChain Tools + RAG Chain。
"""

import json
from typing import TypedDict

from app.guards.safety_guard import check_user_query_safety
from app.tools.rag_tools import rag_answer_tool, eval_report_tool

from langgraph.graph import StateGraph, START, END

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
        return {"route": "eval"}
    return {"route": "rag"}


def rag_node(state: AgentState) -> dict:
    question = state["question"]
    result_str = rag_answer_tool.invoke({"question": question})
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        parsed = {"answer": result_str, "success": True, "sources": []}
    return {
        "answer": parsed.get("answer", ""),
        "tool_result": parsed,
        "sources": parsed.get("sources", []),
    }


def eval_node(state: AgentState) -> dict:
    result_str = eval_report_tool.invoke({"report_type": "all"})
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        parsed = {"error": result_str}

    ret = parsed.get("retrieval", {})
    gen = parsed.get("generation", {})
    summary_parts = []
    if ret:
        summary_parts.append(f'检索评估: {ret.get("passed")}/{ret.get("total")} 通过, final_pass_rate={ret.get("final_pass_rate")}')
    if gen:
        summary_parts.append(f'生成评估: {gen.get("passed")}/{gen.get("total")} 通过, pass_rate={gen.get("pass_rate")}, stable_pass_rate={gen.get("stable_pass_rate")}, flaky={gen.get("flaky_count")}')

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


def run_langgraph_agent(question: str) -> dict:
    graph = build_agent_graph()
    initial: AgentState = {
        "question": question, "safe": True, "route": "",
        "answer": "", "tool_result": {}, "sources": [],
        "reason": "", "category": "",
    }
    return dict(graph.invoke(initial))
