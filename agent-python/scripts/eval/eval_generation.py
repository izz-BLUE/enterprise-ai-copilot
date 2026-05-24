#!/usr/bin/env python3
"""
eval_generation.py — RAG 生成评估脚本（Answer 关键词评估）

对每个 question 调用 rag_service.process_chat() 获取最终 answer，
检查 answer 是否包含预期关键词。调用 LLM，消耗 token。

用法:
    python agent-python/scripts/eval_generation.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

EVAL_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_eval_cases.json')
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports')
REPORT_FILE = os.path.join(REPORTS_DIR, 'generation_eval_report.json')


def _check_prerequisites() -> bool:
    """检查测试集是否存在。"""
    if not os.path.isfile(EVAL_FILE):
        print(f'评估测试集不存在: {EVAL_FILE}')
        print('   请先创建 data/eval/rag_eval_cases.json')
        return False
    return True


def normalize_text(text: str) -> str:
    """文本归一化：消除空格、符号、数字格式差异，减少误判。

    规则：
    1. 去掉所有空白字符（空格、换行、制表符）
    2. 中文全角冒号/逗号/括号 → 英文半角
    3. 全角数字 → 半角数字
    4. 常见同义错字兼容（如 病例本 → 病历本）
    """
    if not text:
        return text

    # 1. 去掉所有空白字符
    text = re.sub(r'\s+', '', text)

    # 2. 中文全角标点 → 英文半角
    text = text.replace('：', ':')   # 全角冒号 ：
    text = text.replace('，', ',')   # 全角逗号 ，
    text = text.replace('（', '(')   # 全角左括号 （
    text = text.replace('）', ')')   # 全角右括号 ）
    text = text.replace('；', ';')   # 全角分号 ；
    text = text.replace('！', '!')   # 全角感叹号 ！
    text = text.replace('？', '?')   # 全角问号 ？

    # 3. 全角数字 → 半角数字
    text = text.translate(str.maketrans(
        '０１２３４５６７８９',
        '0123456789',
    ))

    # 4. 常见同义错字兼容
    text = text.replace('病例本', '病历本')

    return text


def _check_keywords(content: str, expected_keywords: list[str]) -> tuple[bool, list[str], list[str]]:
    """检查所有 expected_keywords 是否出现在 content 中（归一化后比较）。

    返回 (全部命中?, [原始缺失关键词], [归一化后缺失关键词])
    """
    norm_content = normalize_text(content)

    # 原始比较（保留溯源）
    raw_missing = []
    for kw in expected_keywords:
        if kw not in content:
            raw_missing.append(kw)

    # 归一化后比较
    norm_missing = []
    for kw in expected_keywords:
        norm_kw = normalize_text(kw)
        if norm_kw not in norm_content:
            norm_missing.append(kw)

    return len(norm_missing) == 0, raw_missing, norm_missing


def _truncate(text: str, max_len: int = 80) -> str:
    """截断文本用于预览。"""
    text = text.replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + '...'


def _evaluate_case(process_chat, question: str,
                   expected_answer_keywords: list[str]) -> dict:
    """对单个 case 执行一次评估调用，返回结果 dict。"""
    response = process_chat(question)

    if not response.success:
        return {
            'passed': False,
            'keyword_hit': False,
            'raw_missing_keywords': [],
            'norm_missing_keywords': [],
            'answer_preview': 'LLM 调用失败',
            'success': False,
        }

    answer = response.answer

    if not expected_answer_keywords:
        return {
            'passed': True,
            'keyword_hit': True,
            'raw_missing_keywords': [],
            'norm_missing_keywords': [],
            'answer_preview': _truncate(answer),
            'success': True,
        }

    keyword_hit, raw_missing, norm_missing = _check_keywords(
        answer, expected_answer_keywords)
    return {
        'passed': keyword_hit,
        'keyword_hit': keyword_hit,
        'raw_missing_keywords': raw_missing,
        'norm_missing_keywords': norm_missing,
        'answer_preview': _truncate(answer),
        'success': True,
    }


def main():
    # ── 前置检查 ──
    if not _check_prerequisites():
        sys.exit(1)

    # ── 加载测试集 ──
    with open(EVAL_FILE, 'r', encoding='utf-8') as f:
        cases = json.load(f)

    print(f'加载 {len(cases)} 个测试用例\n')

    # ── 延迟导入（避免前置检查失败时因缺少依赖而崩溃）──
    from app.services.rag_service import process_chat

    # ── 表头 ──
    HEADER_FMT = '  {:<5}  {:>6}  {:>5}  {:>4}  {:48}  {}'
    ROW_FMT = '  {:<5}  {:>6}  {:>5}  {:>4}  {:48}  {}'
    print(HEADER_FMT.format('ID', '结果', '状态', '次数', '问题', '缺失关键词'))
    print('  ' + '-' * 110)

    results = []
    flaky_ids = []
    for case in cases:
        case_id = case['id']
        question = case['question']
        expected_answer_keywords: list[str] = case.get('expected_answer_keywords', [])

        # ── 第一次尝试 ──
        r1 = _evaluate_case(process_chat, question, expected_answer_keywords)
        first_passed = r1['passed']

        if first_passed or not r1['success']:
            # 第一次 PASS 或 LLM 调用失败，不 retry
            attempts = 1
            flaky = False
            final_passed = first_passed
            final_r = r1
        else:
            # 第一次 FAIL（非 LLM 错误）→ retry 一次
            r2 = _evaluate_case(process_chat, question, expected_answer_keywords)
            attempts = 2
            final_passed = r2['passed']
            flaky = r2['passed']  # retry 后 PASS 才算 flaky
            final_r = r2 if r2['passed'] else r1  # 优先取 PASS 的结果；都 FAIL 保留第一次

        if flaky:
            flaky_ids.append(case_id)

        results.append({
            'id': case_id,
            'question': question,
            'passed': final_passed,
            'keyword_hit': final_r['keyword_hit'],
            'raw_missing_keywords': final_r.get('raw_missing_keywords', []),
            'norm_missing_keywords': final_r.get('norm_missing_keywords', []),
            'expected_answer_keywords': expected_answer_keywords,
            'answer_preview': final_r['answer_preview'],
            'success': final_r['success'],
            'attempts': attempts,
            'flaky': flaky,
            'first_passed': first_passed,
        })

        status = 'PASS' if final_passed else 'FAIL'
        if flaky:
            kw_tag = '-FLK'
        elif final_r['success'] and not expected_answer_keywords:
            kw_tag = '-SKIP'
        elif not final_r['success']:
            kw_tag = '-ERR'
        else:
            kw_tag = '-OK' if final_r['keyword_hit'] else '-KW'
        display_q = _truncate(question, 46)
        missing_str = ', '.join(final_r.get('norm_missing_keywords', [])) if not final_passed else '-'
        print(ROW_FMT.format(case_id, status, kw_tag, str(attempts), display_q, missing_str))

    # ── 汇总 ──
    total = len(results)
    passed_count = sum(1 for r in results if r['passed'])
    failed_count = total - passed_count
    flaky_count = len(flaky_ids)
    stable_pass_count = sum(1 for r in results if r.get('first_passed', r['passed']))
    success_count = sum(1 for r in results if r['success'])
    llm_fail_count = total - success_count
    pass_rate = (passed_count / total * 100) if total > 0 else 0.0
    stable_pass_rate = (stable_pass_count / total * 100) if total > 0 else 0.0

    print()
    print('=' * 60)
    print('  生成评估结果汇总')
    print('=' * 60)
    print(f'    总用例数:              {total}')
    print(f'    通过(最终):            {passed_count}')
    print(f'    失败:                  {failed_count}')
    if flaky_ids:
        print(f'    flaky case:            {flaky_ids}')
        print(f'    flaky 数量:            {flaky_count}')
    print(f'    LLM 调用失败:          {llm_fail_count}')
    print(f'    pass_rate(最终):       {pass_rate:.1f}%')
    print(f'    stable_pass_rate(首次): {stable_pass_rate:.1f}%')

    # ── 失败用例详情 ──
    if failed_count > 0:
        print()
        print('=' * 60)
        print('  失败用例分析')
        print('=' * 60)
        for r in results:
            if r['passed']:
                continue
            print(f'\n  ID:                       {r["id"]}')
            print(f'  问题:                     {r["question"]}')
            print(f'  LLM 成功:                 {r["success"]}')
            print(f'  尝试次数:                 {r.get("attempts", 1)}')
            print(f'  预期回答关键词:           {r["expected_answer_keywords"]}')
            raw_missing = r.get('raw_missing_keywords', [])
            norm_missing = r.get('norm_missing_keywords', [])
            if raw_missing:
                print(f'  原始缺失关键词:           {raw_missing}')
            if norm_missing:
                print(f'  归一化后缺失关键词:       {norm_missing}')
            if raw_missing and set(raw_missing) != set(norm_missing):
                fixed = set(raw_missing) - set(norm_missing)
                print(f'  归一化修复的关键词:       {list(fixed)}')
            print(f'  回答预览:                 {r["answer_preview"]}')

    # ── 退出码 ──
    _save_report(results, total, passed_count, failed_count,
                 llm_fail_count, pass_rate, flaky_count, stable_pass_rate)
    sys.exit(0 if failed_count == 0 else 1)


def _save_report(results: list[dict], total: int, passed_count: int,
                 failed_count: int, llm_fail_count: int,
                 pass_rate: float, flaky_count: int,
                 stable_pass_rate: float) -> None:
    """将生成评估结果写入 JSON 报告文件。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    slim_cases = []
    for r in results:
        slim_cases.append({
            'id': r['id'],
            'question': r['question'],
            'passed': r['passed'],
            'success': r['success'],
            'attempts': r.get('attempts', 1),
            'flaky': r.get('flaky', False),
            'first_passed': r.get('first_passed', r['passed']),
            'expected_answer_keywords': r['expected_answer_keywords'],
            'raw_missing_keywords': r.get('raw_missing_keywords', []),
            'norm_missing_keywords': r.get('norm_missing_keywords', []),
            'answer_preview': r['answer_preview'],
        })

    report = {
        'eval_type': 'generation',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total': total,
        'passed': passed_count,
        'failed': failed_count,
        'flaky_count': flaky_count,
        'llm_failed': llm_fail_count,
        'pass_rate': round(pass_rate / 100, 4),
        'stable_pass_rate': round(stable_pass_rate / 100, 4),
        'cases': slim_cases,
    }

    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f'\n报告已生成: {REPORT_FILE}')


if __name__ == '__main__':
    main()
