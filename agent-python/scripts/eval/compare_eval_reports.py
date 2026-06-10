#!/usr/bin/env python3
"""
compare_eval_reports.py —— RAG 评估报告回归检查脚本

对比 baseline 和 current 两份 evaluation report，判断 RAG 质量是否退化。

用法:
    python agent-python/scripts/compare_eval_reports.py baseline.json current.json

支持 retrieval 和 generation 两种报告类型，根据 report["eval_type"] 自动区分。

退出码:
    0 = 无回归
    1 = 检测到回归
    2 = 输入错误（文件不存在 / JSON 格式错误 / eval_type 不一致）
"""

import json
import os
import sys


def _load_report(path: str) -> dict:
    """加载并校验 JSON 报告文件。"""
    if not os.path.isfile(path):
        print(f'[错误] 文件不存在: {path}')
        sys.exit(2)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            report = json.load(f)
    except json.JSONDecodeError as e:
        print(f'[错误] JSON 格式错误: {e}')
        sys.exit(2)

    required_keys = {'eval_type', 'total', 'passed', 'failed', 'cases'}
    missing = required_keys - set(report.keys())
    if missing:
        print(f'[错误] 报告缺少必要字段: {missing}')
        sys.exit(2)

    # 兼容新报告格式：如果 current 有 answerable/no-answer 分组字段，打印摘要
    if 'answerable_cases' in report:
        print(f'  answerable_cases={report["answerable_cases"]}, no_answer_cases={report.get("no_answer_cases", 0)}')
    if 'answerable_pass_rate' in report:
        print(f'  answerable_pass_rate={report["answerable_pass_rate"]}, no_answer_pass_rate={report.get("no_answer_pass_rate", 0)}')

    return report


def _build_case_map(cases: list[dict]) -> dict[str, dict]:
    """将 cases 列表按 id 索引为 dict，方便对比。"""
    return {c['id']: c for c in cases}


def _compare_retrieval(baseline: dict, current: dict) -> int:
    """对比 retrieval 报告，返回退出码。"""
    print('=' * 60)
    print('  Retrieval Evaluation Report 对比')
    print('=' * 60)

    # ── 汇总指标对比 ──
    metrics = [
        ('final_pass_rate', 'Final Pass Rate'),
        ('source_hit_rate', 'Source Hit Rate'),
        ('keyword_hit_rate', 'Keyword Hit Rate'),
        ('failed', 'Failed Count'),
    ]

    print()
    for key, label in metrics:
        b_val = baseline[key]
        c_val = current[key]
        delta = c_val - b_val
        direction = '↑' if delta > 0 else '↓' if delta < 0 else '→'
        print(f'  {label:20s}:  baseline={b_val:.4f}  current={c_val:.4f}  ({direction}{abs(delta):.4f})')

    # ── Case 级别对比 ──
    b_cases = _build_case_map(baseline['cases'])
    c_cases = _build_case_map(current['cases'])

    regressed = []   # baseline PASS → current FAIL
    improved = []    # baseline FAIL → current PASS

    for case_id in sorted(set(list(b_cases.keys()) + list(c_cases.keys()))):
        b_passed = b_cases.get(case_id, {}).get('passed', False)
        c_passed = c_cases.get(case_id, {}).get('passed', False)

        if b_passed and not c_passed:
            regressed.append(case_id)
        elif not b_passed and c_passed:
            improved.append(case_id)

    # ── 结论 ──
    c_pass = current.get('final_pass_rate', 0)
    b_pass = baseline.get('final_pass_rate', 0)
    has_regression = c_pass < b_pass or len(regressed) > 0

    print()
    if has_regression:
        print('  >>> REGRESSION DETECTED <<<')
    else:
        print('  >>> NO REGRESSION <<<')

    if regressed:
        print(f'\n  Regressed (PASS -> FAIL): {regressed}')
    if improved:
        print(f'\n  Improved  (FAIL -> PASS): {improved}')
    if not regressed and not improved:
        print('\n  所有 case 结果一致，无变化。')

    return 1 if has_regression else 0


def _compare_generation(baseline: dict, current: dict) -> int:
    """对比 generation 报告，返回退出码。"""
    print('=' * 60)
    print('  Generation Evaluation Report 对比')
    print('=' * 60)

    # ── 汇总指标对比 ──
    metrics = [
        ('pass_rate', 'Pass Rate'),
        ('failed', 'Failed Count'),
        ('llm_failed', 'LLM Failed Count'),
    ]

    print()
    for key, label in metrics:
        b_val = baseline.get(key, 0)
        c_val = current.get(key, 0)
        delta = c_val - b_val
        direction = '↑' if delta > 0 else '↓' if delta < 0 else '→'
        print(f'  {label:20s}:  baseline={b_val:.4f}  current={c_val:.4f}  ({direction}{abs(delta):.4f})')

    # ── Case 级别对比 ──
    b_cases = _build_case_map(baseline['cases'])
    c_cases = _build_case_map(current['cases'])

    regressed = []
    improved = []

    for case_id in sorted(set(list(b_cases.keys()) + list(c_cases.keys()))):
        b_passed = b_cases.get(case_id, {}).get('passed', False)
        c_passed = c_cases.get(case_id, {}).get('passed', False)

        if b_passed and not c_passed:
            regressed.append(case_id)
        elif not b_passed and c_passed:
            improved.append(case_id)

    # ── 结论 ──
    c_pass = current.get('pass_rate', 0)
    b_pass = baseline.get('pass_rate', 0)
    has_regression = c_pass < b_pass or len(regressed) > 0

    print()
    if has_regression:
        print('  >>> REGRESSION DETECTED <<<')
    else:
        print('  >>> NO REGRESSION <<<')

    if regressed:
        print(f'\n  Regressed (PASS -> FAIL): {regressed}')
    if improved:
        print(f'\n  Improved  (FAIL -> PASS): {improved}')
    if not regressed and not improved:
        print('\n  所有 case 结果一致，无变化。')

    return 1 if has_regression else 0


def main():
    if len(sys.argv) != 3:
        print('用法: python agent-python/scripts/compare_eval_reports.py baseline.json current.json')
        print()
        print('示例:')
        print('  python agent-python/scripts/compare_eval_reports.py \\')
        print('    data/eval/reports/generation_eval_report_baseline.json \\')
        print('    data/eval/reports/generation_eval_report.json')
        sys.exit(2)

    baseline_path = sys.argv[1]
    current_path = sys.argv[2]

    print(f'baseline: {baseline_path}')
    print(f'current:  {current_path}')

    baseline = _load_report(baseline_path)
    current = _load_report(current_path)

    # ── eval_type 一致性检查 ──
    if baseline['eval_type'] != current['eval_type']:
        print(f'[错误] eval_type 不一致: baseline={baseline["eval_type"]}, current={current["eval_type"]}')
        sys.exit(2)

    eval_type = baseline['eval_type']
    print(f'eval_type: {eval_type}')

    if eval_type == 'retrieval':
        exit_code = _compare_retrieval(baseline, current)
    elif eval_type == 'generation':
        exit_code = _compare_generation(baseline, current)
    else:
        print(f'[错误] 不支持的 eval_type: {eval_type}')
        sys.exit(2)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
