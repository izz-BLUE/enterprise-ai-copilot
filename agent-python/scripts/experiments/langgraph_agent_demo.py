#!/usr/bin/env python3
"""
langgraph_agent_demo.py —— LangGraph Agent Demo CLI

调用 app.agents.langgraph_agent.run_langgraph_agent()。

用法:
    uv run python scripts/experiments/langgraph_agent_demo.py "病假需要提供哪些材料？"
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.agents.langgraph_agent import run_langgraph_agent


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
