#!/usr/bin/env python3
"""
eval_generation.py — RAG 生成评估脚本（Answer 关键词评估）

对每个 question 调用 rag_service.process_chat() 获取最终 answer，
检查 answer 是否包含预期关键词。调用 LLM，消耗 token。

支持 answerable / no-answer 两类 case：
  - answerable case：检查 answer 关键词命中
  - no-answer case：检查 answer 是否拒绝编造（包含拒答关键词）

用法:
    python agent-python/scripts/eval/eval_generation.py
"""

import argparse
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

# ── 无答案拒答关键词 ─────────────────────────────────────────────
REFUSAL_KEYWORDS = [
    '未找到', '没有找到', '暂无相关', '当前知识库',
    '没有明确依据', '建议联系', '无法确定', '信息不足',
    '未涉及', '未提及', '不在知识库', '没有相关信息',
    '不确定', '请联系', '咨询',
]


def _check_prerequisites() -> bool:
    """检查测试集是否存在。"""
    if not os.path.isfile(EVAL_FILE):
        print(f'评估测试集不存在: {EVAL_FILE}')
        print('   请先创建 data/eval/rag_eval_cases.json')
        return False
    return True


def normalize_text(text: str) -> str:
    """文本归一化：消除空格、符号、数字格式差异，减少误判。"""
    if not text:
        return text

    # 1. 去掉所有空白字符
    text = re.sub(r'\s+', '', text)

    # 2. 中文全角标点 → 英文半角
    text = text.replace('：', ':')
    text = text.replace('，', ',')
    text = text.replace('（', '(')
    text = text.replace('）', ')')
    text = text.replace('；', ';')
    text = text.replace('！', '!')
    text = text.replace('？', '?')

    # 3. 全角数字 → 半角数字
    text = text.translate(str.maketrans(
        '０１２３４５６７８９',
        '0123456789',
    ))

    # 4. 常见同义错字兼容
    text = text.replace('病例本', '病历本')

    # 5. 中文小写数字 → 阿拉伯数字（减少 LLM 措辞差异导致的误判）
    cn_digit_map = {
        '一': '1', '二': '2', '两': '2', '三': '3', '四': '4',
        '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    }
    for cn, ar in cn_digit_map.items():
        text = text.replace(cn, ar)

    return text


def _check_keywords(content: str, expected_keywords: list[str]) -> tuple[bool, list[str], list[str]]:
    """检查所有 expected_keywords 是否出现在 content 中（归一化后比较）。

    返回 (全部命中?, [原始缺失关键词], [归一化后缺失关键词])
    """
    norm_content = normalize_text(content)

    raw_missing = []
    for kw in expected_keywords:
        if kw not in content:
            raw_missing.append(kw)

    norm_missing = []
    for kw in expected_keywords:
        norm_kw = normalize_text(kw)
        if norm_kw not in norm_content:
            norm_missing.append(kw)

    return len(norm_missing) == 0, raw_missing, norm_missing


def _check_refusal(answer: str) -> tuple[bool, list[str]]:
    """检查回答是否包含拒答关键词（表示拒绝编造）。

    返回 (是否拒答?, [命中的拒答关键词])
    """
    norm_answer = normalize_text(answer)
    matched = []
    for kw in REFUSAL_KEYWORDS:
        if kw in norm_answer:
            matched.append(kw)
    return len(matched) > 0, matched


def _truncate(text: str, max_len: int = 80) -> str:
    """截断文本用于预览。"""
    text = text.replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + '...'


def _evaluate_answerable(process_chat, question: str,
                         expected_answer_keywords: list[str]) -> dict:
    """对 answerable case 执行一次评估调用。"""
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


def _evaluate_no_answer(process_chat, question: str) -> dict:
    """对 no-answer case 执行一次评估调用。

    判断标准：回答是否拒绝编造（包含拒答关键词）。
    """
    response = process_chat(question)

    if not response.success:
        return {
            'passed': False,
            'refusal_hit': False,
            'matched_refusal_keywords': [],
            'answer_preview': 'LLM 调用失败',
            'success': False,
        }

    answer = response.answer
    refusal_hit, matched = _check_refusal(answer)

    return {
        'passed': refusal_hit,
        'refusal_hit': refusal_hit,
        'matched_refusal_keywords': matched,
        'answer_preview': _truncate(answer),
        'success': True,
    }


def main():
    # ── 解析命令行参数 ──
    parser = argparse.ArgumentParser(description='RAG 生成评估')
    parser.add_argument('--top-k', type=int, default=3,
                        help='TopK 值（默认 3）')
    parser.add_argument('--retrieval-mode', type=str, default='hybrid',
                        choices=['vector', 'hybrid', 'hybrid_rerank'],
                        help='检索模式：vector / hybrid / hybrid_rerank')
    args = parser.parse_args()
    top_k = args.top_k
    retrieval_mode = args.retrieval_mode

    # ── 前置检查 ──
    if not _check_prerequisites():
        sys.exit(1)

    # ── 加载测试集 ──
    with open(EVAL_FILE, 'r', encoding='utf-8') as f:
        cases = json.load(f)

    answerable_cases = [c for c in cases if c.get('answerable', True)]
    no_answer_cases = [c for c in cases if not c.get('answerable', True)]

    print(f'加载 {len(cases)} 个测试用例 (answerable={len(answerable_cases)}, no_answer={len(no_answer_cases)}, top_k={top_k})\n')

    # ── 延迟导入 ──
    from app.services.rag_service import process_chat
    # 包装 process_chat，注入 top_k 和 retrieval_mode
    _chat = lambda q: process_chat(q, top_k=top_k, retrieval_mode=retrieval_mode)

    # ── 表头 ──
    HEADER_FMT = '  {:<5}  {:>6}  {:>5}  {:>4}  {:>4}  {:38}  {}'
    ROW_FMT = '  {:<5}  {:>6}  {:>5}  {:>4}  {:>4}  {:38}  {}'
    print(HEADER_FMT.format('ID', '结果', '状态', '次数', '类型', '问题', '缺失/拒答'))
    print('  ' + '-' * 110)

    results = []
    flaky_ids = []
    for case in cases:
        case_id = case['id']
        question = case['question']
        expected_answer_keywords: list[str] = case.get('expected_answer_keywords', [])
        answerable = case.get('answerable', True)

        if answerable:
            # ── answerable case：原有逻辑 ──
            r1 = _evaluate_answerable(_chat, question, expected_answer_keywords)
            first_passed = r1['passed']

            if first_passed or not r1['success']:
                attempts = 1
                flaky = False
                final_passed = first_passed
                final_r = r1
            else:
                r2 = _evaluate_answerable(_chat, question, expected_answer_keywords)
                attempts = 2
                final_passed = r2['passed']
                flaky = r2['passed']
                final_r = r2 if r2['passed'] else r1

            if flaky:
                flaky_ids.append(case_id)

            results.append({
                'id': case_id,
                'question': question,
                'answerable': True,
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
            type_tag = '是'
            detail_str = ', '.join(final_r.get('norm_missing_keywords', [])) if not final_passed else '-'

        else:
            # ── no-answer case：检查拒答 ──
            r1 = _evaluate_no_answer(_chat, question)
            first_passed = r1['passed']

            if first_passed or not r1['success']:
                attempts = 1
                flaky = False
                final_passed = first_passed
                final_r = r1
            else:
                r2 = _evaluate_no_answer(_chat, question)
                attempts = 2
                final_passed = r2['passed']
                flaky = r2['passed']
                final_r = r2 if r2['passed'] else r1

            if flaky:
                flaky_ids.append(case_id)

            results.append({
                'id': case_id,
                'question': question,
                'answerable': False,
                'passed': final_passed,
                'refusal_hit': final_r['refusal_hit'],
                'matched_refusal_keywords': final_r.get('matched_refusal_keywords', []),
                'answer_preview': final_r['answer_preview'],
                'success': final_r['success'],
                'attempts': attempts,
                'flaky': flaky,
                'first_passed': first_passed,
            })

            status = 'PASS' if final_passed else 'FAIL'
            if flaky:
                kw_tag = '-FLK'
            elif not final_r['success']:
                kw_tag = '-ERR'
            else:
                kw_tag = '+REF' if final_r['refusal_hit'] else '-REF'
            type_tag = '否'
            detail_str = ','.join(final_r.get('matched_refusal_keywords', [])) if final_r.get('matched_refusal_keywords') else '-'

        display_q = _truncate(question, 36)
        print(ROW_FMT.format(case_id, status, kw_tag, str(attempts), type_tag, display_q, detail_str))

    # ── 汇总 ──
    ab_results = [r for r in results if r['answerable']]
    na_results = [r for r in results if not r['answerable']]

    total = len(results)
    ab_total = len(ab_results)
    na_total = len(na_results)

    ab_passed = sum(1 for r in ab_results if r['passed'])
    ab_failed = ab_total - ab_passed
    ab_pass_rate = (ab_passed / ab_total * 100) if ab_total > 0 else 0.0

    na_passed = sum(1 for r in na_results if r['passed'])
    na_failed = na_total - na_passed
    na_pass_rate = (na_passed / na_total * 100) if na_total > 0 else 0.0

    total_passed = ab_passed + na_passed
    total_failed = ab_failed + na_failed
    overall_pass_rate = (total_passed / total * 100) if total > 0 else 0.0

    flaky_count = len(flaky_ids)
    stable_pass_count = sum(1 for r in results if r.get('first_passed', r['passed']))
    stable_pass_rate = (stable_pass_count / total * 100) if total > 0 else 0.0
    llm_fail_count = sum(1 for r in results if not r['success'])

    print()
    print('=' * 60)
    print('  生成评估结果汇总')
    print('=' * 60)
    print(f'    总用例数:                {total}')
    print(f'    ---')
    print(f'    answerable 用例数:       {ab_total}')
    print(f'    answerable 通过:         {ab_passed}')
    print(f'    answerable 失败:         {ab_failed}')
    print(f'    answerable_pass_rate:    {ab_pass_rate:.1f}%')
    print(f'    ---')
    print(f'    no-answer 用例数:        {na_total}')
    print(f'    no-answer 通过(拒答):    {na_passed}')
    print(f'    no-answer 失败(编造):    {na_failed}')
    print(f'    no_answer_pass_rate:     {na_pass_rate:.1f}%')
    print(f'    ---')
    print(f'    overall_pass_rate:       {overall_pass_rate:.1f}%')
    print(f'    stable_pass_rate(首次):  {stable_pass_rate:.1f}%')
    if flaky_ids:
        print(f'    flaky case:              {flaky_ids}')
        print(f'    flaky 数量:              {flaky_count}')
    print(f'    LLM 调用失败:            {llm_fail_count}')

    # ── 失败用例详情 ──
    if total_failed > 0:
        print()
        print('=' * 60)
        print('  失败用例分析')
        print('=' * 60)
        for r in results:
            if r['passed']:
                continue
            print(f'\n  ID:                       {r["id"]}')
            print(f'  问题:                     {r["question"]}')
            print(f'  answerable:               {r["answerable"]}')
            print(f'  LLM 成功:                 {r["success"]}')
            print(f'  尝试次数:                 {r.get("attempts", 1)}')
            if r['answerable']:
                print(f'  预期回答关键词:           {r.get("expected_answer_keywords", [])}')
                raw_missing = r.get('raw_missing_keywords', [])
                norm_missing = r.get('norm_missing_keywords', [])
                if raw_missing:
                    print(f'  原始缺失关键词:           {raw_missing}')
                if norm_missing:
                    print(f'  归一化后缺失关键词:       {norm_missing}')
            else:
                print(f'  拒答关键词命中:           {r.get("matched_refusal_keywords", [])}')
            print(f'  回答预览:                 {r["answer_preview"]}')

    # ── 退出码 ──
    _save_report(results, total, ab_total, na_total,
                 ab_passed, ab_failed, ab_pass_rate,
                 na_passed, na_failed, na_pass_rate,
                 total_passed, total_failed, overall_pass_rate,
                 llm_fail_count, flaky_count, stable_pass_rate)
    sys.exit(0 if total_failed == 0 else 1)


def _save_report(results: list[dict], total: int,
                 ab_total: int, na_total: int,
                 ab_passed: int, ab_failed: int, ab_pass_rate: float,
                 na_passed: int, na_failed: int, na_pass_rate: float,
                 total_passed: int, total_failed: int, overall_pass_rate: float,
                 llm_fail_count: int, flaky_count: int,
                 stable_pass_rate: float) -> None:
    """将生成评估结果写入 JSON 报告文件。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    slim_cases = []
    for r in results:
        entry = {
            'id': r['id'],
            'question': r['question'],
            'answerable': r['answerable'],
            'passed': r['passed'],
            'success': r['success'],
            'attempts': r.get('attempts', 1),
            'flaky': r.get('flaky', False),
            'first_passed': r.get('first_passed', r['passed']),
            'answer_preview': r['answer_preview'],
        }
        if r['answerable']:
            entry['expected_answer_keywords'] = r.get('expected_answer_keywords', [])
            entry['raw_missing_keywords'] = r.get('raw_missing_keywords', [])
            entry['norm_missing_keywords'] = r.get('norm_missing_keywords', [])
        else:
            entry['matched_refusal_keywords'] = r.get('matched_refusal_keywords', [])
        slim_cases.append(entry)

    report = {
        'eval_type': 'generation',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total': total,
        'answerable_cases': ab_total,
        'no_answer_cases': na_total,
        'answerable_passed': ab_passed,
        'answerable_failed': ab_failed,
        'answerable_pass_rate': round(ab_pass_rate / 100, 4),
        'no_answer_passed': na_passed,
        'no_answer_failed': na_failed,
        'no_answer_pass_rate': round(na_pass_rate / 100, 4),
        'passed': total_passed,
        'failed': total_failed,
        'overall_pass_rate': round(overall_pass_rate / 100, 4),
        'flaky_count': flaky_count,
        'llm_failed': llm_fail_count,
        'pass_rate': round(overall_pass_rate / 100, 4),
        'stable_pass_rate': round(stable_pass_rate / 100, 4),
        'cases': slim_cases,
    }

    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f'\n报告已生成: {REPORT_FILE}')


if __name__ == '__main__':
    main()
