#!/usr/bin/env python3
"""
langgraph_agent_demo.py —— LangGraph Agent Loop Demo CLI

显式启用 use_planner=True，调用 app.agents.langgraph_agent.run_langgraph_agent()
走 Planner ⇄ Tool Executor 拓扑。

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
    result = run_langgraph_agent(question, allow_eval=True, use_planner=True)

    print(f'route:       {result.get("route", "")}')
    print(f'stop_reason: {result.get("stop_reason", "")}')
    print(f'step_count:  {result.get("step_count", 0)}')
    tool_history = result.get('tool_history', []) or []
    print(f'tool_history: {len(tool_history)} entries')
    for idx, entry in enumerate(tool_history, start=1):
        print(f'  [{idx}] {entry.get("tool_name")} -> {entry.get("status")}')


if __name__ == '__main__':
    main()
