#!/usr/bin/env python3
"""
run_rag_eval.py —— 一键运行 RAG 评估流程

依次执行检索评估和生成评估，汇总结果。
可选通过 --compare-generation / --compare-retrieval 对比 baseline 做回归检查。

用法:
    python agent-python/scripts/run_rag_eval.py
    python agent-python/scripts/run_rag_eval.py --compare-generation baseline.json
    python agent-python/scripts/run_rag_eval.py --compare-retrieval baseline.json
"""

import os
import subprocess
import sys

# ── 路径 ──────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))

RETRIEVAL_SCRIPT = os.path.join(_SCRIPT_DIR, 'eval_retrieval.py')
GENERATION_SCRIPT = os.path.join(_SCRIPT_DIR, 'eval_generation.py')
COMPARE_SCRIPT = os.path.join(_SCRIPT_DIR, 'compare_eval_reports.py')

GENERATION_REPORT = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'generation_eval_report.json')
RETRIEVAL_REPORT = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'retrieval_eval_report.json')

GENERATION_BASELINE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'baselines', 'generation_eval_baseline.json')
RETRIEVAL_BASELINE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'baselines', 'retrieval_eval_baseline.json')


def _run_step(name: str, cmd: list[str]) -> int:
    """运行一个子步骤，打印输出并返回退出码。"""
    print(f'\n{"=" * 60}')
    print(f'  {name}')
    print(f'{"=" * 60}')
    print(f'  命令: {" ".join(cmd)}\n')
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode


def main():
    failed_steps: list[str] = []

    # ── 1. retrieval eval ──
    rc = _run_step('Step 1/2: Retrieval Evaluation',
                   [sys.executable, RETRIEVAL_SCRIPT])
    if rc != 0:
        failed_steps.append('retrieval_eval')

    # ── 2. generation eval ──
    rc = _run_step('Step 2/2: Generation Evaluation',
                   [sys.executable, GENERATION_SCRIPT])
    if rc != 0:
        failed_steps.append('generation_eval')

    # ── 3. 可选 regression 对比 ──
    args = sys.argv[1:]
    use_baseline = '--with-baseline' in args

    if '--compare-generation' in args:
        idx = args.index('--compare-generation')
        if idx + 1 < len(args):
            baseline = args[idx + 1]
            rc = _run_step('Regression Check: Generation',
                           [sys.executable, COMPARE_SCRIPT, baseline, GENERATION_REPORT])
            if rc != 0:
                failed_steps.append('generation_regression')

    if '--compare-retrieval' in args:
        idx = args.index('--compare-retrieval')
        if idx + 1 < len(args):
            baseline = args[idx + 1]
            rc = _run_step('Regression Check: Retrieval',
                           [sys.executable, COMPARE_SCRIPT, baseline, RETRIEVAL_REPORT])
            if rc != 0:
                failed_steps.append('retrieval_regression')

    if use_baseline:
        for name, baseline_path, current_path in [
            ('retrieval', RETRIEVAL_BASELINE, RETRIEVAL_REPORT),
            ('generation', GENERATION_BASELINE, GENERATION_REPORT),
        ]:
            if not os.path.isfile(baseline_path):
                print(f'\n  提示: {name} baseline 不存在 ({baseline_path})')
                print(f'  请先运行 update_eval_baseline.py 初始化 baseline。')
                failed_steps.append(f'{name}_baseline_missing')
                continue
            rc = _run_step(f'Regression Check: {name}',
                           [sys.executable, COMPARE_SCRIPT, baseline_path, current_path])
            if rc != 0:
                failed_steps.append(f'{name}_regression')

    # ── 最终结论 ──
    print()
    print('=' * 60)
    if failed_steps:
        print(f'  RAG Evaluation FAILED')
        print(f'  失败步骤: {", ".join(failed_steps)}')
        print('=' * 60)
        sys.exit(1)
    else:
        print(f'  RAG Evaluation PASSED')
        print('=' * 60)
        sys.exit(0)


if __name__ == '__main__':
    main()
