#!/usr/bin/env python3
"""
compare_topk_eval.py — TopK 对比评估脚本

对不同 TopK 配置分别运行 retrieval 和 generation evaluation，
对比 pass_rate、耗时等指标，输出对比报告。

用法:
    python agent-python/scripts/eval/compare_topk_eval.py
    python agent-python/scripts/eval/compare_topk_eval.py --top-k-list 3,5,8
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# ── 路径 ──────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))

RETRIEVAL_SCRIPT = os.path.join(_SCRIPT_DIR, 'eval_retrieval.py')
GENERATION_SCRIPT = os.path.join(_SCRIPT_DIR, 'eval_generation.py')
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports')
COMPARISON_FILE = os.path.join(REPORTS_DIR, 'topk_eval_comparison.json')
COMPARISON_MD = os.path.join(REPORTS_DIR, 'topk_eval_comparison.md')


def _run_eval(name: str, script: str, top_k: int) -> tuple[int, float]:
    """运行一次 eval 脚本，返回 (退出码, 耗时秒)。"""
    cmd = [sys.executable, script, '--top-k', str(top_k)]
    print(f'\n  [{name}] top_k={top_k}  命令: {" ".join(cmd)}')
    start = time.time()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.time() - start
    print(f'  [{name}] top_k={top_k}  耗时: {elapsed:.1f}s  退出码: {result.returncode}')
    return result.returncode, elapsed


def _load_report(path: str) -> dict | None:
    """加载 JSON 报告，不存在则返回 None。"""
    if not os.path.isfile(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _extract_retrieval_metrics(report: dict) -> dict:
    """从 retrieval 报告提取关键指标。"""
    return {
        'answerable_cases': report.get('answerable_cases', 0),
        'no_answer_cases': report.get('no_answer_cases', 0),
        'passed': report.get('passed', 0),
        'failed': report.get('failed', 0),
        'final_pass_rate': report.get('final_pass_rate', 0),
        'source_hit_rate': report.get('source_hit_rate', 0),
        'keyword_hit_rate': report.get('keyword_hit_rate', 0),
    }


def _extract_generation_metrics(report: dict) -> dict:
    """从 generation 报告提取关键指标。"""
    return {
        'answerable_cases': report.get('answerable_cases', 0),
        'no_answer_cases': report.get('no_answer_cases', 0),
        'answerable_pass_rate': report.get('answerable_pass_rate', 0),
        'no_answer_pass_rate': report.get('no_answer_pass_rate', 0),
        'overall_pass_rate': report.get('overall_pass_rate', 0),
        'pass_rate': report.get('pass_rate', 0),
        'flaky_count': report.get('flaky_count', 0),
        'llm_failed': report.get('llm_failed', 0),
    }


def main():
    parser = argparse.ArgumentParser(description='TopK 对比评估')
    parser.add_argument('--top-k-list', type=str, default='3,5,8',
                        help='TopK 值列表，逗号分隔（默认 3,5,8）')
    args = parser.parse_args()
    top_k_list = [int(x.strip()) for x in args.top_k_list.split(',')]

    print('=' * 60)
    print(f'  TopK 对比评估  top_k_list={top_k_list}')
    print('=' * 60)

    results = []
    for top_k in top_k_list:
        print(f'\n{"=" * 60}')
        print(f'  TopK = {top_k}')
        print(f'{"=" * 60}')

        # ── retrieval eval ──
        retrieval_report_path = os.path.join(REPORTS_DIR, 'retrieval_eval_report.json')
        rc, retrieval_elapsed = _run_eval('Retrieval', RETRIEVAL_SCRIPT, top_k)
        retrieval_report = _load_report(retrieval_report_path)
        retrieval_metrics = _extract_retrieval_metrics(retrieval_report) if retrieval_report else {}

        # ── generation eval ──
        generation_report_path = os.path.join(REPORTS_DIR, 'generation_eval_report.json')
        rc, generation_elapsed = _run_eval('Generation', GENERATION_SCRIPT, top_k)
        generation_report = _load_report(generation_report_path)
        generation_metrics = _extract_generation_metrics(generation_report) if generation_report else {}

        total_elapsed = retrieval_elapsed + generation_elapsed

        entry = {
            'top_k': top_k,
            'retrieval': {
                **retrieval_metrics,
                'elapsed_seconds': round(retrieval_elapsed, 1),
            },
            'generation': {
                **generation_metrics,
                'elapsed_seconds': round(generation_elapsed, 1),
            },
            'total_elapsed_seconds': round(total_elapsed, 1),
        }
        results.append(entry)

    # ── 生成报告 ──
    os.makedirs(REPORTS_DIR, exist_ok=True)

    comparison = {
        'eval_type': 'topk_comparison',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'top_k_list': top_k_list,
        'results': results,
    }

    with open(COMPARISON_FILE, 'w', encoding='utf-8') as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)

    # ── 生成 Markdown 报告 ──
    md_lines = [
        '# TopK 对比评估报告',
        '',
        f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        '',
        '## 对比结果',
        '',
        '| TopK | Retrieval pass_rate | Generation answerable | Generation no-answer | Generation overall | Flaky | Retrieval 耗时 | Generation 耗时 | 总耗时 |',
        '| ---- | ------------------- | --------------------- | -------------------- | ------------------ | ----- | ------------- | -------------- | ------ |',
    ]

    for r in results:
        tk = r['top_k']
        rp = r['retrieval'].get('final_pass_rate', 0)
        ga = r['generation'].get('answerable_pass_rate', 0)
        gn = r['generation'].get('no_answer_pass_rate', 0)
        go = r['generation'].get('overall_pass_rate', 0)
        fl = r['generation'].get('flaky_count', 0)
        re_t = r['retrieval'].get('elapsed_seconds', 0)
        ge_t = r['generation'].get('elapsed_seconds', 0)
        to_t = r['total_elapsed_seconds']
        md_lines.append(
            f'| {tk} | {rp:.0%} | {ga:.0%} | {gn:.0%} | {go:.0%} | {fl} | {re_t:.1f}s | {ge_t:.1f}s | {to_t:.1f}s |'
        )

    # ── 结论 ──
    md_lines.extend([
        '',
        '## 结论',
        '',
    ])

    if len(results) >= 2:
        best = min(results, key=lambda r: r['total_elapsed_seconds'])
        all_pass = all(
            r['retrieval'].get('final_pass_rate', 0) == 1.0 and
            r['generation'].get('overall_pass_rate', 0) == 1.0
            for r in results
        )

        if all_pass:
            md_lines.append('- 所有 TopK 配置下 retrieval 和 generation 均 100% 通过。')
            md_lines.append(f'- 推荐默认 TopK={results[0]["top_k"]}（当前值），在召回质量和成本之间更平衡。')
            md_lines.append(f'- TopK 增大不提升通过率，但增加检索范围和 token 成本。')
        else:
            for r in results:
                tk = r['top_k']
                rp = r['retrieval'].get('final_pass_rate', 0)
                ga = r['generation'].get('answerable_pass_rate', 0)
                if rp < 1.0 or ga < 1.0:
                    md_lines.append(f'- TopK={tk} 存在未通过用例（retrieval={rp:.0%}, generation_answerable={ga:.0%}）。')

    md_lines.extend([
        '',
        '## 面试可讲点',
        '',
        '> TopK 不是越大越好，而是在召回率、噪声、成本和延迟之间做平衡。',
        '',
    ])

    with open(COMPARISON_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md_lines))

    # ── 终端打印对比表 ──
    print()
    print('=' * 60)
    print('  TopK 对比结果')
    print('=' * 60)
    print()
    print(f'  {"TopK":>4}  {"Retrieval":>10}  {"Gen_AB":>8}  {"Gen_NA":>8}  {"Gen_All":>8}  {"Flaky":>5}  {"耗时":>8}')
    print('  ' + '-' * 65)
    for r in results:
        tk = r['top_k']
        rp = r['retrieval'].get('final_pass_rate', 0)
        ga = r['generation'].get('answerable_pass_rate', 0)
        gn = r['generation'].get('no_answer_pass_rate', 0)
        go = r['generation'].get('overall_pass_rate', 0)
        fl = r['generation'].get('flaky_count', 0)
        to_t = r['total_elapsed_seconds']
        print(f'  {tk:>4}  {rp:>9.0%}  {ga:>7.0%}  {gn:>7.0%}  {go:>7.0%}  {fl:>5}  {to_t:>6.1f}s')

    print(f'\n报告已生成: {COMPARISON_FILE}')
    print(f'报告已生成: {COMPARISON_MD}')


if __name__ == '__main__':
    main()
