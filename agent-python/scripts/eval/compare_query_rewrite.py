#!/usr/bin/env python3
"""
compare_query_rewrite.py — Query Rewrite 对比评估脚本

对不同 rewrite_mode 配置分别运行 retrieval 和 generation evaluation，
对比 pass_rate、rewrite 命中率等指标，输出对比报告。

用法:
    python agent-python/scripts/eval/compare_query_rewrite.py
    python agent-python/scripts/eval/compare_query_rewrite.py --retrieval-mode hybrid
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
COMPARISON_FILE = os.path.join(REPORTS_DIR, 'query_rewrite_comparison.json')
COMPARISON_MD = os.path.join(REPORTS_DIR, 'query_rewrite_comparison.md')

REWRITE_MODES = ['none', 'rule']


def _run_eval(name: str, script: str, retrieval_mode: str,
              rewrite_mode: str) -> tuple[int, float]:
    """运行一次 eval 脚本，返回 (退出码, 耗时秒)。"""
    cmd = [sys.executable, script,
           '--retrieval-mode', retrieval_mode,
           '--rewrite-mode', rewrite_mode]
    print(f'\n  [{name}] retrieval_mode={retrieval_mode}, rewrite_mode={rewrite_mode}')
    print(f'  命令: {" ".join(cmd)}')
    start = time.time()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.time() - start
    print(f'  [{name}] 耗时: {elapsed:.1f}s  退出码: {result.returncode}')
    return result.returncode, elapsed


def _load_report(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _extract_retrieval_metrics(report: dict) -> dict:
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


def _count_rewrites(report: dict | None) -> list[dict]:
    """从 retrieval 报告中提取 rewrite 命中信息。"""
    if not report:
        return []
    rewrites = []
    for case in report.get('cases', []):
        if case.get('rewrite_applied', False):
            rewrites.append({
                'id': case['id'],
                'question': case['question'],
                'retrieval_query': case.get('retrieval_query', ''),
                'rewrite_reason': case.get('rewrite_reason', ''),
            })
    return rewrites


def main():
    parser = argparse.ArgumentParser(description='Query Rewrite 对比评估')
    parser.add_argument('--retrieval-mode', type=str, default='hybrid',
                        choices=['vector', 'hybrid', 'hybrid_rerank'],
                        help='检索模式（默认 hybrid）')
    args = parser.parse_args()
    retrieval_mode = args.retrieval_mode

    print('=' * 60)
    print(f'  Query Rewrite 对比评估  retrieval_mode={retrieval_mode}')
    print(f'  rewrite_modes={REWRITE_MODES}')
    print('=' * 60)

    results = []
    for rewrite_mode in REWRITE_MODES:
        print(f'\n{"=" * 60}')
        print(f'  Rewrite Mode = {rewrite_mode}')
        print(f'{"=" * 60}')

        # ── retrieval eval ──
        retrieval_report_path = os.path.join(REPORTS_DIR, 'retrieval_eval_report.json')
        rc, retrieval_elapsed = _run_eval(
            'Retrieval', RETRIEVAL_SCRIPT, retrieval_mode, rewrite_mode)
        retrieval_report = _load_report(retrieval_report_path)
        retrieval_metrics = _extract_retrieval_metrics(retrieval_report) if retrieval_report else {}
        rewrites = _count_rewrites(retrieval_report)

        # ── generation eval ──
        generation_report_path = os.path.join(REPORTS_DIR, 'generation_eval_report.json')
        rc, generation_elapsed = _run_eval(
            'Generation', GENERATION_SCRIPT, retrieval_mode, rewrite_mode)
        generation_report = _load_report(generation_report_path)
        generation_metrics = _extract_generation_metrics(generation_report) if generation_report else {}

        total_elapsed = retrieval_elapsed + generation_elapsed

        entry = {
            'rewrite_mode': rewrite_mode,
            'retrieval_mode': retrieval_mode,
            'retrieval': {
                **retrieval_metrics,
                'elapsed_seconds': round(retrieval_elapsed, 1),
            },
            'generation': {
                **generation_metrics,
                'elapsed_seconds': round(generation_elapsed, 1),
            },
            'total_elapsed_seconds': round(total_elapsed, 1),
            'rewrites': rewrites,
            'rewrite_count': len(rewrites),
        }
        results.append(entry)

    # ── 生成 JSON 报告 ──
    os.makedirs(REPORTS_DIR, exist_ok=True)

    comparison = {
        'eval_type': 'query_rewrite_comparison',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'retrieval_mode': retrieval_mode,
        'rewrite_modes': REWRITE_MODES,
        'results': results,
    }

    with open(COMPARISON_FILE, 'w', encoding='utf-8') as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)

    # ── 生成 Markdown 报告 ──
    md_lines = [
        '# Query Rewrite 对比评估报告',
        '',
        f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        '',
        f'检索模式: `{retrieval_mode}`',
        '',
        '## 对比结果',
        '',
        '| Rewrite Mode '
        '| Retrieval pass_rate '
        '| Generation answerable '
        '| Generation no-answer '
        '| Generation overall '
        '| Flaky '
        '| Rewrite 命中 '
        '| Retrieval 耗时 '
        '| Generation 耗时 '
        '| 总耗时 |',
        '| ------------- '
        '| ------------------- '
        '| --------------------- '
        '| -------------------- '
        '| ------------------ '
        '| ----- '
        '| ----------- '
        '| ------------- '
        '| -------------- '
        '| ------ |',
    ]

    for r in results:
        rm = r['rewrite_mode']
        rp = r['retrieval'].get('final_pass_rate', 0)
        ga = r['generation'].get('answerable_pass_rate', 0)
        gn = r['generation'].get('no_answer_pass_rate', 0)
        go = r['generation'].get('overall_pass_rate', 0)
        fl = r['generation'].get('flaky_count', 0)
        rw = r['rewrite_count']
        re_t = r['retrieval'].get('elapsed_seconds', 0)
        ge_t = r['generation'].get('elapsed_seconds', 0)
        to_t = r['total_elapsed_seconds']
        md_lines.append(
            f'| {rm} | {rp:.0%} | {ga:.0%} | {gn:.0%} '
            f'| {go:.0%} | {fl} | {rw} '
            f'| {re_t:.1f}s | {ge_t:.1f}s | {to_t:.1f}s |'
        )

    # ── Rewrite 命中详情 ──
    all_rewrites = []
    for r in results:
        for rw in r.get('rewrites', []):
            rw['rewrite_mode'] = r['rewrite_mode']
            all_rewrites.append(rw)

    if all_rewrites:
        md_lines.extend([
            '',
            '## Rewrite 命中详情',
            '',
            '| Case ID | Rewrite Mode | 原始问题 | 重写后查询 | 原因 |',
            '| ------- | ------------- | -------- | ---------- | ---- |',
        ])
        for rw in all_rewrites:
            md_lines.append(
                f'| {rw["id"]} | {rw["rewrite_mode"]} '
                f'| {rw["question"]} | {rw["retrieval_query"]} '
                f'| {rw["rewrite_reason"]} |'
            )

    # ── 结论 ──
    md_lines.extend(['', '## 结论', ''])

    if len(results) >= 2:
        none_r = next((r for r in results if r['rewrite_mode'] == 'none'), None)
        rule_r = next((r for r in results if r['rewrite_mode'] == 'rule'), None)

        if none_r and rule_r:
            none_gen = none_r['generation'].get('overall_pass_rate', 0)
            rule_gen = rule_r['generation'].get('overall_pass_rate', 0)
            none_ret = none_r['retrieval'].get('final_pass_rate', 0)
            rule_ret = rule_r['retrieval'].get('final_pass_rate', 0)

            if rule_gen > none_gen:
                md_lines.append(f'- rule 模式 generation overall 通过率 ({rule_gen:.0%}) 高于 none ({none_gen:.0%})。')
            if rule_gen < none_gen:
                md_lines.append(
                    f'- rule 模式 generation overall 通过率 '
                    f'({rule_gen:.0%}) 低于 none ({none_gen:.0%})，'
                    f'存在 regression。'
                )

            if rule_ret > none_ret:
                md_lines.append(f'- rule 模式 retrieval pass_rate ({rule_ret:.0%}) 高于 none ({none_ret:.0%})。')
            elif rule_ret == none_ret:
                md_lines.append(f'- 两种模式 retrieval pass_rate 相同 ({rule_ret:.0%})。')
            else:
                md_lines.append(
                    f'- rule 模式 retrieval pass_rate '
                    f'({rule_ret:.0%}) 低于 none ({none_ret:.0%})，'
                    f'存在 regression。'
                )

            rewrite_count = rule_r.get('rewrite_count', 0)
            md_lines.append(f'- rule 模式命中 {rewrite_count} 条 rewrite 规则。')

            if rule_r.get('rewrites'):
                md_lines.append('- 命中的 case:')
                for rw in rule_r['rewrites']:
                    md_lines.append(f'  - {rw["id"]}: "{rw["question"]}" → "{rw["retrieval_query"]}"')

            # Regression 判断
            has_regression = rule_gen < none_gen or rule_ret < none_ret
            if has_regression:
                md_lines.extend([
                    '',
                    '**结论：rule 模式存在 regression，不建议默认启用。**',
                ])
            else:
                md_lines.extend([
                    '',
                    '**结论：rule 模式未引入 regression。**',
                    '当前知识库规模较小（33 chunks），口语化问题的检索已经比较稳定。',
                    'rule 模式作为实验能力保留，不切默认。',
                ])

    md_lines.extend([
        '',
        '## 面试可讲点',
        '',
        '> Query Rewrite 的核心价值：用户口语化表达与知识库正式表述之间存在 gap，',
        '> 规则匹配可以在不引入 LLM 不确定性的前提下，将口语化问题映射为更精确的检索 query。',
        '> 这是一个 "检索层优化" 而非 "生成层优化"，不改变最终 prompt 中的用户问题。',
        '',
    ])

    with open(COMPARISON_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md_lines))

    # ── 终端打印 ──
    print()
    print('=' * 60)
    print('  Query Rewrite 对比结果')
    print('=' * 60)
    print()
    print(f'  {"Mode":>6}  {"Retrieval":>10}  {"Gen_AB":>8}  '
          f'{"Gen_NA":>8}  {"Gen_All":>8}  {"Flaky":>5}  '
          f'{"Rewrite":>8}  {"耗时":>8}')
    print('  ' + '-' * 70)
    for r in results:
        rm = r['rewrite_mode']
        rp = r['retrieval'].get('final_pass_rate', 0)
        ga = r['generation'].get('answerable_pass_rate', 0)
        gn = r['generation'].get('no_answer_pass_rate', 0)
        go = r['generation'].get('overall_pass_rate', 0)
        fl = r['generation'].get('flaky_count', 0)
        rw = r['rewrite_count']
        to_t = r['total_elapsed_seconds']
        print(f'  {rm:>6}  {rp:>9.0%}  {ga:>7.0%}  {gn:>7.0%}  {go:>7.0%}  {fl:>5}  {rw:>8}  {to_t:>6.1f}s')

    print(f'\n报告已生成: {COMPARISON_FILE}')
    print(f'报告已生成: {COMPARISON_MD}')


if __name__ == '__main__':
    main()
