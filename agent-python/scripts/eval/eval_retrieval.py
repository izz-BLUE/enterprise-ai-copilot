#!/usr/bin/env python3
"""
eval_retrieval.py — RAG 检索评估脚本（Source + Keyword 双层评估）

评估 hybrid_retriever 的 TopK 是否命中预期知识来源，
以及预期关键词是否出现在 TopK chunk content 中。
不调用 LLM，不消耗 token。

支持 answerable / no-answer 两类 case：
  - answerable case：检查 source_hit + keyword_hit
  - no-answer case：只记录检索结果，单独统计，不判 fail

用法:
    python agent-python/scripts/eval/eval_retrieval.py

依赖:
    - data/eval/rag_eval_cases.json（测试集）
    - data/processed/faiss.index（需先运行 build_faiss_index.py）
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

EVAL_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_eval_cases.json')
FAISS_INDEX = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports')
REPORT_FILE = os.path.join(REPORTS_DIR, 'retrieval_eval_report.json')

TOP_K = 3


def _check_prerequisites() -> bool:
    """检查测试集和 Faiss 索引导出的前置条件。"""
    ok = True

    if not os.path.isfile(EVAL_FILE):
        print(f'评估测试集不存在: {EVAL_FILE}')
        print('   请先创建 data/eval/rag_eval_cases.json')
        ok = False

    if not os.path.isfile(FAISS_INDEX):
        print(f'Faiss 索引不存在: {FAISS_INDEX}')
        print('   请先运行 python agent-python/scripts/build_faiss_index.py')
        ok = False

    return ok


def _check_keywords(content: str, expected_keywords: list[str]) -> tuple[bool, list[str]]:
    """检查所有 expected_keywords 是否出现在 content 中。

    返回 (全部命中?, [缺失的关键词列表])
    """
    missing = []
    for kw in expected_keywords:
        if kw not in content:
            missing.append(kw)
    return len(missing) == 0, missing


def main():
    # ── 解析命令行参数 ──
    parser = argparse.ArgumentParser(description='RAG 检索评估')
    parser.add_argument('--top-k', type=int, default=TOP_K,
                        help=f'TopK 值（默认 {TOP_K}）')
    parser.add_argument('--retrieval-mode', type=str, default='hybrid',
                        choices=['vector', 'hybrid', 'hybrid_rerank'],
                        help='检索模式：vector / hybrid / hybrid_rerank')
    parser.add_argument('--rewrite-mode', type=str, default='none',
                        choices=['none', 'rule'],
                        help='查询重写模式：none / rule')
    parser.add_argument('--min-source-hit-rate', type=float, default=100.0,
                        help='source_hit_rate 最低阈值（百分比，默认 100.0）')
    parser.add_argument('--min-keyword-hit-rate', type=float, default=100.0,
                        help='keyword_hit_rate 最低阈值（百分比，默认 100.0）')
    parser.add_argument('--min-final-pass-rate', type=float, default=100.0,
                        help='final_pass_rate 最低阈值（百分比，默认 100.0）')
    args = parser.parse_args()
    top_k = args.top_k
    retrieval_mode = args.retrieval_mode
    rewrite_mode = args.rewrite_mode
    min_source_hit_rate = args.min_source_hit_rate
    min_keyword_hit_rate = args.min_keyword_hit_rate
    min_final_pass_rate = args.min_final_pass_rate

    # ── 验证阈值参数 ──
    for name, value in [('min-source-hit-rate', min_source_hit_rate),
                        ('min-keyword-hit-rate', min_keyword_hit_rate),
                        ('min-final-pass-rate', min_final_pass_rate)]:
        if not (0.0 <= value <= 100.0):
            print(f'错误: --{name} 必须在 0～100 之间，当前值: {value}')
            sys.exit(1)

    # ── 前置检查 ──
    if not _check_prerequisites():
        sys.exit(1)

    # ── 加载测试集 ──
    with open(EVAL_FILE, 'r', encoding='utf-8') as f:
        cases = json.load(f)

    # ── 区分 answerable / no-answer ──
    answerable_cases = [c for c in cases if c.get('answerable', True)]
    no_answer_cases = [c for c in cases if not c.get('answerable', True)]

    print(f'加载 {len(cases)} 个测试用例 (answerable={len(answerable_cases)}, no_answer={len(no_answer_cases)})\n')

    # ── 导入检索器（延迟导入，避免前置检查失败时因缺少依赖而崩溃） ──
    from app.retrieval.hybrid_retriever import retrieve
    from app.retrieval.query_rewriter import rewrite_query

    # ── 表头 ──
    HEADER_FMT = '  {:<5}  {:>6}  {:>5}  {:>5}  {:>4}  {:38}  {}'
    ROW_FMT = '  {:<5}  {:>6}  {:>5}  {:>5}  {:>4}  {:38}  {}'
    print(HEADER_FMT.format('ID', '结果', 'SRC', 'KW', '类型', '问题', '预期来源'))
    print('  ' + '-' * 120)

    results = []
    for case in cases:
        case_id = case['id']
        question = case['question']
        expected_sources: list[str] = case.get('expected_sources', [])
        expected_keywords: list[str] = case.get('expected_keywords', [])
        answerable = case.get('answerable', True)

        # 调用检索器（可选 query rewrite）
        rewrite_result = rewrite_query(question, mode=rewrite_mode)
        retrieval_query = rewrite_result['rewritten_query']
        topk = retrieve(retrieval_query, top_k=top_k, mode=retrieval_mode)

        # 提取实际 source_file（去重）
        actual_sources = sorted({r['source_file'] for r in topk})
        top_chunk_ids = [r['id'] for r in topk]

        if answerable:
            # ── answerable case：原有逻辑 ──
            source_hit = any(es in actual_sources for es in expected_sources)

            all_content = '\n'.join(r['content'] for r in topk)
            keyword_hit = True
            missing_keywords: list[str] = []
            if expected_keywords:
                keyword_hit, missing_keywords = _check_keywords(all_content, expected_keywords)

            if expected_keywords:
                passed = source_hit and keyword_hit
            else:
                passed = source_hit

            results.append({
                'id': case_id,
                'question': question,
                'retrieval_query': retrieval_query,
                'rewrite_applied': rewrite_result['rewrite_applied'],
                'rewrite_reason': rewrite_result['rewrite_reason'],
                'answerable': True,
                'passed': passed,
                'source_hit': source_hit,
                'keyword_hit': keyword_hit,
                'missing_keywords': missing_keywords,
                'expected_sources': expected_sources,
                'expected_keywords': expected_keywords,
                'actual_sources': actual_sources,
                'top_chunk_ids': top_chunk_ids,
            })

            status = 'PASS' if passed else 'FAIL'
            src_tag = '+SRC' if source_hit else '-SRC'
            kw_tag = '+KW ' if keyword_hit else '-KW '
        else:
            # ── no-answer case：只记录检索结果，不判 fail ──
            results.append({
                'id': case_id,
                'question': question,
                'retrieval_query': retrieval_query,
                'rewrite_applied': rewrite_result['rewrite_applied'],
                'rewrite_reason': rewrite_result['rewrite_reason'],
                'answerable': False,
                'passed': True,  # no-answer case 在 retrieval 层不判 fail
                'source_hit': False,
                'keyword_hit': False,
                'missing_keywords': [],
                'expected_sources': [],
                'expected_keywords': [],
                'actual_sources': actual_sources,
                'top_chunk_ids': top_chunk_ids,
            })

            status = 'SKIP'
            src_tag = ' N/A'
            kw_tag = ' N/A'

        display_question = question if len(question) <= 36 else question[:33] + '...'
        type_tag = '是' if answerable else '否'
        print(ROW_FMT.format(case_id, status, src_tag, kw_tag, type_tag,
                             display_question, ', '.join(expected_sources) if expected_sources else '-'))

    # ── 汇总（只统计 answerable case）──
    answerable_results = [r for r in results if r['answerable']]
    no_answer_results = [r for r in results if not r['answerable']]

    total = len(results)
    ab_total = len(answerable_results)
    na_total = len(no_answer_results)

    source_hit_count = sum(1 for r in answerable_results if r['source_hit'])
    keyword_hit_count = sum(1 for r in answerable_results if r['keyword_hit'])
    passed_count = sum(1 for r in answerable_results if r['passed'])
    failed_count = ab_total - passed_count
    source_hit_rate = (source_hit_count / ab_total * 100) if ab_total > 0 else 0.0
    keyword_hit_rate = (keyword_hit_count / ab_total * 100) if ab_total > 0 else 0.0
    final_pass_rate = (passed_count / ab_total * 100) if ab_total > 0 else 0.0

    print()
    print('=' * 60)
    print('  评估结果汇总')
    print('=' * 60)
    print(f'    总用例数:              {total}')
    print(f'    answerable 用例数:     {ab_total}')
    print(f'    no-answer 用例数:      {na_total}')
    print(f'    answerable 通过:       {passed_count}')
    print(f'    answerable 失败:       {failed_count}')
    print(f'    source_hit_rate:       {source_hit_rate:.1f}%')
    print(f'    keyword_hit_rate:      {keyword_hit_rate:.1f}%')
    print(f'    final_pass_rate:       {final_pass_rate:.1f}%')

    # ── 阈值配置 ──
    print()
    print('=' * 60)
    print('  质量门禁阈值')
    print('=' * 60)
    print(f'    min_source_hit_rate:   {min_source_hit_rate:.1f}%')
    print(f'    min_keyword_hit_rate:  {min_keyword_hit_rate:.1f}%')
    print(f'    min_final_pass_rate:   {min_final_pass_rate:.1f}%')

    # ── 阈值判定 ──
    source_pass = source_hit_rate >= min_source_hit_rate
    keyword_pass = keyword_hit_rate >= min_keyword_hit_rate
    final_pass = final_pass_rate >= min_final_pass_rate
    threshold_passed = source_pass and keyword_pass and final_pass

    print()
    print('=' * 60)
    print('  门禁判定结果')
    print('=' * 60)
    print(f'    source_hit_rate:       {"PASS" if source_pass else "FAIL"} ({source_hit_rate:.1f}% >= {min_source_hit_rate:.1f}%)')
    print(f'    keyword_hit_rate:      {"PASS" if keyword_pass else "FAIL"} ({keyword_hit_rate:.1f}% >= {min_keyword_hit_rate:.1f}%)')
    print(f'    final_pass_rate:       {"PASS" if final_pass else "FAIL"} ({final_pass_rate:.1f}% >= {min_final_pass_rate:.1f}%)')
    print(f'    threshold_passed:      {"PASS" if threshold_passed else "FAIL"}')

    # ── 失败用例详情 ──
    if failed_count > 0:
        print()
        print('=' * 60)
        print('  失败用例分析')
        print('=' * 60)
        for r in results:
            if r['passed'] or not r['answerable']:
                continue
            print(f'\n  ID:               {r["id"]}')
            print(f'  问题:             {r["question"]}')
            print(f'  source_hit:       {r["source_hit"]}')
            print(f'  keyword_hit:      {r["keyword_hit"]}')
            if r['missing_keywords']:
                print(f'  缺失关键词:       {r["missing_keywords"]}')
            print(f'  预期来源:         {r["expected_sources"]}')
            print(f'  实际来源:         {r["actual_sources"]}')
            print(f'  TopK chunk IDs:   {r["top_chunk_ids"]}')

    # ── 退出码（基于阈值判定）──
    _save_report(results, total, ab_total, na_total,
                 passed_count, failed_count,
                 source_hit_rate, keyword_hit_rate, final_pass_rate,
                 min_source_hit_rate, min_keyword_hit_rate, min_final_pass_rate,
                 threshold_passed, top_k, rewrite_mode, retrieval_mode)
    sys.exit(0 if threshold_passed else 1)


def _get_git_sha() -> str:
    """尝试获取当前 Git commit SHA，失败返回 'unknown'。"""
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return 'unknown'


def _file_sha256(path: str) -> str | None:
    """计算文件 SHA-256，文件不存在返回 None。"""
    import hashlib
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _save_report(results: list[dict], total: int,
                 ab_total: int, na_total: int,
                 passed_count: int, failed_count: int,
                 source_hit_rate: float, keyword_hit_rate: float,
                 final_pass_rate: float,
                 min_source_hit_rate: float, min_keyword_hit_rate: float,
                 min_final_pass_rate: float, threshold_passed: bool,
                 top_k: int = TOP_K,
                 rewrite_mode: str = 'none',
                 retrieval_mode: str = 'hybrid') -> None:
    """将评估结果写入 JSON 报告文件。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    report = {
        'eval_type': 'retrieval',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'rewrite_mode': rewrite_mode,
        'retrieval_mode': retrieval_mode,
        'top_k': top_k,
        'total': total,
        'answerable_cases': ab_total,
        'no_answer_cases': na_total,
        'git_commit_sha': _get_git_sha(),
        'test_set_sha256': _file_sha256(EVAL_FILE),
        'faiss_index_sha256': _file_sha256(FAISS_INDEX),
        'passed': passed_count,
        'failed': failed_count,
        'source_hit_rate': round(source_hit_rate / 100, 4),
        'keyword_hit_rate': round(keyword_hit_rate / 100, 4),
        'final_pass_rate': round(final_pass_rate / 100, 4),
        'thresholds': {
            'min_source_hit_rate': min_source_hit_rate / 100,
            'min_keyword_hit_rate': min_keyword_hit_rate / 100,
            'min_final_pass_rate': min_final_pass_rate / 100,
        },
        'threshold_passed': threshold_passed,
        'cases': results,
    }

    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f'\n报告已生成: {REPORT_FILE}')


if __name__ == '__main__':
    main()
