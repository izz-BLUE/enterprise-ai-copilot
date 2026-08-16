"""run_agent_benchmark.py —— 真实模型 Agent Eval（手工运行，非 CI 门禁）

与 tests/test_agent_eval.py 的确定性回归不同，本脚本调用真实 Planner LLM
与真实 Tool，输出每个 Case 的实际行为与期望对比，供人工分析。

明确区分：
  - 确定性回归结果  → tests/test_agent_eval.py（mock/stub，CI 门禁）
  - 真实模型评估结果 → 本脚本输出（真实模型，不稳定，非门禁）

前置条件：DEEPSEEK_API_KEY 已配置；RAG / Eval 数据产物已构建。

运行：
  cd agent-python
  uv run python scripts/eval/run_agent_benchmark.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_eval.cases import AGENT_EVAL_CASES  # noqa: E402
from app.agent_eval.runner import _executed_sequence  # noqa: E402
from app.agents.langgraph_agent import run_langgraph_agent  # noqa: E402


def main() -> int:
    report = {
        'kind': 'real_model_eval',
        'note': '真实模型评估结果，非 CI 门禁；结果仅反映当前模型行为，不用于回归判定',
        'cases': [],
    }
    for case in AGENT_EVAL_CASES:
        entry = {
            'case_id': case.case_id,
            'question': case.question,
            'allow_eval': case.allow_eval,
            'expected_stop_reason': case.expected_stop_reason,
            'expected_tool_sequence': list(case.expected_tool_sequence),
            'actual': None,
            'error': None,
        }
        try:
            state = run_langgraph_agent(
                case.question,
                allow_eval=case.allow_eval,
                allow_business_actions=case.allow_business_actions,
                business_date=case.business_date,
                trace_id=f'bench-{case.case_id}',
                use_planner=True,
            )
            entry['actual'] = {
                'stop_reason': state.get('stop_reason', ''),
                'tool_sequence': _executed_sequence(state.get('tool_history', [])),
                'step_count': state.get('step_count', 0),
                'tool_call_count': state.get('tool_call_count', 0),
                'route': state.get('route', ''),
                'answer_head': (state.get('answer') or '')[:120],
            }
        except Exception as exc:  # noqa: BLE001 —— 单条 Case 失败不影响其余
            entry['error'] = f'{type(exc).__name__}: {exc}'
        report['cases'].append(entry)
        print(json.dumps(entry, ensure_ascii=False))

    summary = {
        'total': len(report['cases']),
        'errored': sum(1 for c in report['cases'] if c['error']),
        'stop_reason_matched': sum(
            1 for c in report['cases']
            if c['actual'] and c['actual']['stop_reason'] == c['expected_stop_reason']
        ),
        'tool_sequence_matched': sum(
            1 for c in report['cases']
            if c['actual'] and c['actual']['tool_sequence'] == c['expected_tool_sequence']
        ),
    }
    report['summary'] = summary
    out = Path(__file__).resolve().parent / 'agent_benchmark_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n摘要: {summary}')
    print(f'报告已写入: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
