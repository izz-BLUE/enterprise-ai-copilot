"""
rag_tools.py —— RAG 工具封装

将现有的 RAG 问答能力和评估报告查询能力封装为 LangChain Tool，
供后续 Agent 工作流调用。
"""

import json
import os

from langchain_core.tools import tool

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))

RETRIEVAL_REPORT = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'retrieval_eval_report.json')
GENERATION_REPORT = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'generation_eval_report.json')


@tool
def rag_answer_tool(question: str) -> str:
    """回答企业制度、流程、IT 文档、HR 文档等知识库问题。

    输入: question - 用户提出的企业知识库相关问题
    返回: JSON 字符串，包含 answer（回答内容）、success（是否成功）、
          sources（引用来源列表）。
    """
    from app.chains.langchain_rag_chain import answer_with_langchain_rag

    result = answer_with_langchain_rag(question)

    output = {
        "answer": result["answer"],
        "success": result["success"],
        "sources": [s["id"] for s in result["sources"]],
    }
    return json.dumps(output, ensure_ascii=False)


@tool
def eval_report_tool(report_type: str) -> str:
    """查询当前 RAG 评估报告状态。

    输入: report_type - 报告类型，可选值:
          "retrieval" - 仅返回检索评估摘要
          "generation" - 仅返回生成评估摘要
          "all"        - 返回全部摘要
    返回: JSON 字符串，包含各项评估指标。
    """
    result: dict = {}

    if report_type in ("retrieval", "all"):
        if os.path.isfile(RETRIEVAL_REPORT):
            with open(RETRIEVAL_REPORT, 'r', encoding='utf-8') as f:
                r = json.load(f)
            result["retrieval"] = {
                "final_pass_rate": r.get("final_pass_rate"),
                "source_hit_rate": r.get("source_hit_rate"),
                "keyword_hit_rate": r.get("keyword_hit_rate"),
                "total": r.get("total"),
                "passed": r.get("passed"),
                "failed": r.get("failed"),
            }
        else:
            result["retrieval"] = {
                "error": "报告不存在，请先运行 python agent-python/scripts/eval/run_rag_eval.py"
            }

    if report_type in ("generation", "all"):
        if os.path.isfile(GENERATION_REPORT):
            with open(GENERATION_REPORT, 'r', encoding='utf-8') as f:
                r = json.load(f)
            result["generation"] = {
                "pass_rate": r.get("pass_rate"),
                "stable_pass_rate": r.get("stable_pass_rate"),
                "flaky_count": r.get("flaky_count"),
                "total": r.get("total"),
                "passed": r.get("passed"),
                "failed": r.get("failed"),
                "llm_failed": r.get("llm_failed"),
            }
        else:
            result["generation"] = {
                "error": "报告不存在，请先运行 python agent-python/scripts/eval/run_rag_eval.py"
            }

    return json.dumps(result, ensure_ascii=False, indent=2)
