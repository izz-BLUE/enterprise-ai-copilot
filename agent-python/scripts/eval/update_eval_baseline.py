#!/usr/bin/env python3
"""
update_eval_baseline.py —— RAG 评估 Baseline 更新脚本

将当前 reports 目录下的评估报告复制到 baselines 目录。
只在当前报告全部通过时才允许更新。

用法:
    python agent-python/scripts/eval/update_eval_baseline.py
"""

import json
import os
import shutil
import sys

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))

REPORTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports')
BASELINES_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'baselines')

RETRIEVAL_CURRENT = os.path.join(REPORTS_DIR, 'retrieval_eval_report.json')
GENERATION_CURRENT = os.path.join(REPORTS_DIR, 'generation_eval_report.json')
RETRIEVAL_BASELINE = os.path.join(BASELINES_DIR, 'retrieval_eval_baseline.json')
GENERATION_BASELINE = os.path.join(BASELINES_DIR, 'generation_eval_baseline.json')

REQUIRED = [
    ('retrieval', RETRIEVAL_CURRENT, 'final_pass_rate'),
    ('generation', GENERATION_CURRENT, 'pass_rate'),
]


def main():
    os.makedirs(BASELINES_DIR, exist_ok=True)
    errors: list[str] = []

    for name, path, rate_key in REQUIRED:
        # 1. 检查文件存在
        if not os.path.isfile(path):
            errors.append(f'{name}: 当前报告不存在 ({path})')
            continue

        # 2. 加载并检查通过状态
        with open(path, 'r', encoding='utf-8') as f:
            report = json.load(f)

        rate = report.get(rate_key, 0)
        if abs(rate - 1.0) > 0.0001:
            errors.append(
                f'{name}: 未通过 ({rate_key}={rate}, 需要 1.0)')
            continue

        # 3. 复制为 baseline
        basename = f'{name}_eval_baseline.json'
        dest = os.path.join(BASELINES_DIR, basename)
        shutil.copy2(path, dest)
        print(f'  已更新: {dest}')

    if errors:
        print(f'\n[错误] 以下报告不满足 baseline 更新条件:')
        for e in errors:
            print(f'  - {e}')
        print('\n'
              '请先运行评估脚本并确保所有报告都通过。\n'
              '  python agent-python/scripts/eval/run_rag_eval.py')
        sys.exit(1)

    print(f'\nBaseline 更新完成。\n')
    print('现在可以使用质量门禁:')
    print('  python agent-python/scripts/eval/run_rag_eval.py --with-baseline')


if __name__ == '__main__':
    main()
