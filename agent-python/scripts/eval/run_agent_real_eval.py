#!/usr/bin/env python3
"""run_agent_real_eval.py —— Real Agent Eval CLI（手工运行）

用法（手工真实评估，不进 CI）：
    cd agent-python
    uv run python scripts/eval/run_agent_real_eval.py                 # 全部 Case × 3 Run
    uv run python scripts/eval/run_agent_real_eval.py --runs 1        # 全部 Case × 1 Run
    uv run python scripts/eval/run_agent_real_eval.py --case R01-...    # 仅某条 Case
    uv run python scripts/eval/run_agent_real_eval.py --category single_rag

⚠️ 本脚本会调用真实 DeepSeek LLM（Planner 必须真实），Tool 输出仍走 Stub。
   需要 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 已配置。
   若环境变量缺失，脚本直接退出（不进入任何 fallback 路径）。

输出：
    data/eval/reports/agent_real_eval_<UTC 时间戳>.json
    data/eval/reports/agent_real_eval_<UTC 时间戳>.json.bak  ← 上一份报告备份
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本可以 import app.* 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_real_eval.cases import (
    CATEGORY_LABELS,
    REAL_AGENT_EVAL_SUITE_VERSION,
    case_by_id,
)  # noqa: E402
from app.agent_real_eval.runner import (  # noqa: E402
    iter_cases,
    run_real_eval,
    write_report,
)
from app.core.config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
)

REPORTS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'eval' / 'reports'


def _require_real_llm_env() -> None:
    """Real Eval 必须使用真实 Planner LLM：环境变量缺失直接拒绝执行。"""
    missing = []
    if not DEEPSEEK_API_KEY:
        missing.append('DEEPSEEK_API_KEY')
    if not DEEPSEEK_BASE_URL:
        missing.append('DEEPSEEK_BASE_URL')
    if not DEEPSEEK_MODEL:
        missing.append('DEEPSEEK_MODEL')
    if missing:
        sys.stderr.write(
            'Real Eval 必须使用真实 DeepSeek LLM；缺少环境变量: '
            + ', '.join(missing)
            + '\n'
            '请在 agent-python/.env 或当前 shell 中配置后重试。\n'
        )
        sys.exit(2)


def _rotate_reports(target: Path) -> None:
    """把已有目标文件改名为 .bak（只留一份历史），保证报告时间戳唯一性。"""
    if target.exists():
        backup = target.with_suffix(target.suffix + '.bak')
        if backup.exists():
            backup.unlink()
        target.rename(backup)


def _print_summary(report_dict: dict) -> None:
    """终端最后打印简洁中文摘要。"""
    metrics = report_dict['metrics']
    total_cases = metrics['total_cases']
    total_runs = metrics['total_runs']
    pass_rate = metrics['run_pass_rate']
    stable_rate = metrics.get('stable_case_rate', 0.0)
    print('\n===== Real Agent Eval 摘要 =====')
    print(f'套件版本: {report_dict["suite_version"]}')
    print(f'模型: {report_dict["model"]} (temperature={report_dict["temperature"]})')
    print(f'Case 数: {total_cases}    Run 总数: {total_runs}')
    print(f'run_pass_rate:                {pass_rate:.2%}')
    if stable_rate is None:
        stable_str = 'N/A (runs_per_case=1, 该指标在此配置下不可用)'
    else:
        stable_str = f'{stable_rate:.2%}'
    print(f'stable_case_rate:             {stable_str}')
    print(f'required_tool_satisfied_rate: {metrics["required_tool_satisfied_rate"]:.2%}')
    print(f'run_sequence_match_rate:      {metrics.get("run_sequence_match_rate", 0):.2%}')
    print(f'required_task_coverage_rate:  {metrics.get("required_task_coverage_rate", 0):.2%}')
    print(f'trajectory_consistency_rate:  {metrics.get("trajectory_consistency_rate", 0):.2%}')
    print(f'finish_when_complete_rate:    {metrics["finish_when_complete_rate"]:.2%}')
    print(f'invalid_decision_rate:        {metrics["invalid_decision_rate"]:.2%}')
    print(f'unauthorized_tool_attempt_rate:   {metrics["unauthorized_tool_attempt_rate"]:.2%}')
    print(f'unauthorized_tool_execution_rate: {metrics["unauthorized_tool_execution_rate"]:.2%}')
    print(f'redundant_tool_attempt_rate:  {metrics["redundant_tool_attempt_rate"]:.2%}')
    print(f'budget_exhaustion_rate:       {metrics["budget_exhaustion_rate"]:.2%}')
    print(f'avg_step_count:               {metrics["average_step_count"]}')
    print(f'avg_tool_call_count:          {metrics["average_tool_call_count"]}')
    print(f'latency p50/p95/avg (ms):     '
          f'{metrics["latency_p50_ms"]} / {metrics["latency_p95_ms"]} / {metrics["latency_avg_ms"]}')

    # 失败 Case / 不稳定 Case
    failed = report_dict.get('failed_case_ids') or []
    unstable = report_dict.get('unstable_case_ids') or []
    if failed:
        print(f'\n失败 Case ({len(failed)}): {", ".join(failed)}')
    else:
        print('\n失败 Case: 0')
    if unstable:
        print(f'不稳定 Case ({len(unstable)}): {", ".join(unstable)}')

    failure_by_reason = metrics.get('failure_by_reason') or {}
    if failure_by_reason:
        print('\n失败原因聚合:')
        for reason, count in failure_by_reason.items():
            print(f'  - {reason}: {count}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Real Agent Eval —— Planner 必须真实，Tool 使用 Stub',
    )
    parser.add_argument(
        '--runs',
        type=int,
        default=3,
        help='每条 Case 重复运行次数（默认 3）',
    )
    parser.add_argument(
        '--case',
        type=str,
        default=None,
        help='只运行指定 case_id（例：R08-rag-then-eval）',
    )
    parser.add_argument(
        '--category',
        type=str,
        default=None,
        choices=sorted(set(CATEGORY_LABELS.keys())),
        help='只运行某 category',
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='报告输出路径（默认 data/eval/reports/agent_real_eval_<UTC>.json）',
    )
    args = parser.parse_args(argv)

    _require_real_llm_env()

    # 选择 Case
    cases = list(iter_cases(case_id=args.case, category=args.category))
    if not cases:
        sys.stderr.write('筛选条件下没有 Case 可运行。\n')
        return 2

    print(f'开始 Real Agent Eval：Case={len(cases)} runs={args.runs}', flush=True)
    print(f'场景类别: {sorted({c.category for c in cases})}')

    # 真实 LLM 调用入口（runner 包 wrapper，但不替换为 mock）
    from app.services.llm_service import call_llm as real_call_llm  # noqa: PLC0415

    suite = run_real_eval(
        cases=cases,
        runs_per_case=args.runs,
        real_call_llm=real_call_llm,
        temperature=DEEPSEEK_TEMPERATURE,
    )

    # 报告路径
    if args.output:
        output_path = args.output
    else:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = suite.timestamp.replace(':', '').replace('-', '')
        output_path = str(
            REPORTS_DIR / f'agent_real_eval_{stamp}.json'
        )
    _rotate_reports(Path(output_path))
    write_report(suite, output_path)

    # 转 dict 以便打印
    report_dict = suite_to_jsonable(suite)
    _print_summary(report_dict)
    print(f'\n报告已写入: {output_path}')
    return 0


def suite_to_jsonable(suite) -> dict:
    from dataclasses import asdict
    return asdict(suite)


if __name__ == '__main__':
    raise SystemExit(main())
