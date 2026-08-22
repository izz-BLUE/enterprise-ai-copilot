#!/usr/bin/env python3
"""e2e_composite_proposal.py —— 真实 Planner 组合 E2E（临时验证脚本，不进 CI）

三个场景，全部使用真实 Planner（DeepSeek）+ 真实 leave_proposal_tool
（内部走真实 plan_annual_leave_action 受控链路）；leave_balance_tool 的
Java 内部接口用 stub 隔离，不产生任何 Java 写操作。

    A. 余额够 + 字段完整：RAG → balance → leave_proposal_tool → finish
    B. 余额不足：RAG → balance → finish（不得调用 leave_proposal_tool）
    C. 简单直接申请：leave_proposal_tool → finish（不要求先 RAG/balance）

用法：cd agent-python && uv run python scripts/eval/e2e_composite_proposal.py
需要 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents.langgraph_agent import run_langgraph_agent  # noqa: E402

BUSINESS_DATE = date(2026, 8, 18)
EMPLOYEE_ID = 'E1001'


def _stub_java_client(annual_balance: float):
    """Java 内部只读接口 stub：返回固定年假余额，不发起真实 HTTP。"""

    class _Stub:
        def get_leave_balance(self, employee_id: str, trace_id: str) -> dict:
            return {
                'annualBalance': annual_balance,
                'updatedAt': '2026-08-18T00:00:00Z',
            }

        def list_leave_requests(self, employee_id: str, trace_id: str,
                                limit: int | None = None) -> dict:
            return {'total': 0, 'items': []}

    return _Stub()


def _run_scenario(name: str, question: str, annual_balance: float) -> dict:
    print(f'\n{"=" * 72}\n场景 {name}: {question}\n余额 stub: {annual_balance} 天')
    with patch('app.tools.enterprise_tools.get_java_client',
               return_value=_stub_java_client(annual_balance)):
        result = run_langgraph_agent(
            question,
            allow_business_actions=True,
            business_date=BUSINESS_DATE,
            trace_id=f'e2e-proposal-{name}',
            use_planner=True,
            employee_id=EMPLOYEE_ID,
        )
    return result


def _dump(name: str, question: str, annual_balance: float) -> None:
    result = _run_scenario(name, question, annual_balance)
    print(f'--- 最终状态 ---')
    print(f'stop_reason:      {result.get("stop_reason")}')
    print(f'step_count:       {result.get("step_count")}')
    print(f'tool_call_count:  {result.get("tool_call_count")}')
    executed = [
        entry['tool_name']
        for entry in result.get('tool_history', [])
        if entry.get('status') in ('success', 'error')
    ]
    print(f'executed_tool_sequence: {executed}')
    print('tool_history:')
    for entry in result.get('tool_history', []):
        print(f'  - {entry["tool_name"]} status={entry["status"]} '
              f'args={json.dumps(entry.get("arguments"), ensure_ascii=False)}')
        print(f'    observation={str(entry.get("observation"))[:220]}')
    print(f'action_proposal: {json.dumps(result.get("action_proposal"), ensure_ascii=False, default=str)}')
    print(f'missing_fields:  {result.get("missing_fields")}')
    print(f'answer:          {str(result.get("answer"))[:200]}')
    print(f'planner_decision(最终): {json.dumps(result.get("planner_decision"), ensure_ascii=False, default=str)}')


def main() -> int:
    _dump(
        'A-sufficient-full-fields',
        '先查连续休5天的公司规定，再看看我剩多少年假，'
        '够的话帮我准备2026-09-01到2026-09-05的年假申请，原因是回家探亲',
        annual_balance=10.0,
    )
    _dump(
        'B-insufficient-balance',
        '先查连续休5天的公司规定，再看看我剩多少年假，'
        '够的话帮我准备2026-09-01到2026-09-05的年假申请，原因是回家探亲',
        annual_balance=2.0,
    )
    _dump(
        'C-direct-apply',
        '帮我准备2026-09-01到2026-09-05的年假申请，原因是回家探亲',
        annual_balance=10.0,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
