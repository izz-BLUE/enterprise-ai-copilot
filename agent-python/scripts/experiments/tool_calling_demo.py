#!/usr/bin/env python3
"""
tool_calling_demo.py —— Tool Calling Demo

基于规则路由选择工具：评估类问题 → eval_report_tool，其他 → rag_answer_tool。

用法:
    python agent-python/scripts/experiments/tool_calling_demo.py "病假需要提供哪些材料？"
    python agent-python/scripts/experiments/tool_calling_demo.py "当前RAG评估通过率是多少？"
"""

import os
import sys

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.guards.safety_guard import check_user_query_safety
from app.tools.rag_tools import rag_answer_tool, eval_report_tool

# ── 路由规则 ───────────────────────────────────────────────
EVAL_KEYWORDS = ['评估', '通过率', 'pass_rate', '命中率', 'baseline', '回归', 'flaky']


def _route(question: str) -> str:
    """简单关键词路由: eval 相关 → eval_report_tool, 否则 → rag_answer_tool。"""
    if any(kw in question.lower() for kw in EVAL_KEYWORDS):
        return 'eval_report_tool'
    return 'rag_answer_tool'


def main():
    if len(sys.argv) < 2:
        print('用法: python agent-python/scripts/experiments/tool_calling_demo.py "<问题>"')
        sys.exit(1)

    question = sys.argv[1]

    print(f'用户问题: {question}')

    # ── Safety Guard ──
    safety = check_user_query_safety(question)
    if not safety["safe"]:
        print(f'选择的工具: safety_guard')
        print(f'类别: {safety["category"]}')
        print(f'原因: {safety["reason"]}')
        print(f'\n{"=" * 60}')
        print(f'{safety["message"]}')
        print(f'{"=" * 60}')
        sys.exit(0)

    tool_name = _route(question)
    print(f'选择的工具: {tool_name}')

    if tool_name == 'eval_report_tool':
        result = eval_report_tool.invoke({"report_type": "all"})
    else:
        result = rag_answer_tool.invoke({"question": question})

    print(f'\n{"=" * 60}')
    print(f'工具返回:\n{result}')
    print(f'{"=" * 60}')


if __name__ == '__main__':
    main()
