#!/usr/bin/env python3
"""
langgraph_agent_demo.py —— LangGraph 最小 Agent Workflow Demo

实现 safety → router → (rag | eval | refuse) 的最小状态图，
集成 Safety Guard + LangChain Tools + RAG Chain。

用法:
    uv run python scripts/experiments/langgraph_agent_demo.py "病假需要提供哪些材料？"
    uv run python scripts/experiments/langgraph_agent_demo.py "当前RAG评估通过率是多少？"
    uv run python scripts/experiments/langgraph_agent_demo.py "怎么伪造病假证明？"
"""

import json
import os
import sys
from typing import TypedDict

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.guards.safety_guard import check_user_query_safety
from app.tools.rag_tools import rag_answer_tool, eval_report_tool

from langgraph.graph import StateGraph, START, END

# ── 路由关键词 ─────────────────────────────────────────────
EVAL_KEYWORDS = ['评估', '通过率', 'pass_rate', '命中率', 'baseline', '回归', 'flaky']


# ═══════════════════════════════════════════════════════════
# 一、State 定义
# ═══════════════════════════════════════════════════════════

class AgentState(TypedDict):
    question: str
    safe: bool
    route: str
    answer: str
    tool_result: dict
    sources: list
    reason: str
    category: str


# ═══════════════════════════════════════════════════════════
# 二、节点函数
# ═══════════════════════════════════════════════════════════

def safety_node(state: AgentState) -> dict:
    """安全守卫：调用 check_user_query_safety。"""
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
    return {
        "safe": True,
        "reason": "",
        "category": "normal",
    }


def router_node(state: AgentState) -> dict:
    """路由分发：根据安全结果 + 关键词决定下一步。"""
    if not state.get("safe", True):
        return {"route": "refuse"}

    question = state["question"]
    if any(kw in question.lower() for kw in EVAL_KEYWORDS):
        return {"route": "eval"}
    return {"route": "rag"}


def rag_node(state: AgentState) -> dict:
    """RAG 回答：调用 rag_answer_tool 获取知识库答案。"""
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
    """评估查询：调用 eval_report_tool 获取评估摘要。"""
    result_str = eval_report_tool.invoke({"report_type": "all"})
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        parsed = {"error": result_str}

    # 用中文总结评估状态
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
    """拒答：直接返回已有的拒答文案。"""
    answer = state.get("answer", "")
    if not answer:
        answer = "抱歉，我不能协助处理该请求。"
    return {"answer": answer}


# ═══════════════════════════════════════════════════════════
# 三、图构建
# ═══════════════════════════════════════════════════════════

def build_agent_graph():
    """构建 LangGraph Agent 状态图。"""
    graph = StateGraph(AgentState)

    # 节点注册
    graph.add_node("safety_node", safety_node)
    graph.add_node("router_node", router_node)
    graph.add_node("rag_node", rag_node)
    graph.add_node("eval_node", eval_node)
    graph.add_node("refuse_node", refuse_node)

    # 边
    graph.add_edge(START, "safety_node")
    graph.add_edge("safety_node", "router_node")

    # 条件边：根据 route 分发
    graph.add_conditional_edges(
        "router_node",
        lambda state: state.get("route", "rag"),
        {
            "rag": "rag_node",
            "eval": "eval_node",
            "refuse": "refuse_node",
        },
    )

    # 终点
    graph.add_edge("rag_node", END)
    graph.add_edge("eval_node", END)
    graph.add_edge("refuse_node", END)

    return graph.compile()


def run_langgraph_agent(question: str) -> dict:
    """运行 Agent 工作流，返回最终 state dict。"""
    graph = build_agent_graph()
    initial: AgentState = {
        "question": question,
        "safe": True,
        "route": "",
        "answer": "",
        "tool_result": {},
        "sources": [],
        "reason": "",
        "category": "",
    }
    return dict(graph.invoke(initial))


# ═══════════════════════════════════════════════════════════
# 四、Demo CLI
# ═══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print('用法: uv run python scripts/experiments/langgraph_agent_demo.py "<问题>"')
        sys.exit(1)

    question = sys.argv[1]
    result = run_langgraph_agent(question)

    print(f'用户问题: {result["question"]}')
    print(f'route:    {result["route"]}')
    print(f'safe:     {result["safe"]}')
    print(f'category: {result["category"]}')
    if result["reason"]:
        print(f'reason:   {result["reason"]}')
    print(f'\n{"=" * 60}')
    print(f'最终回答:\n{result["answer"]}')
    print(f'{"=" * 60}')
    if result.get("sources"):
        print(f'sources: {result["sources"]}')


if __name__ == '__main__':
    main()
